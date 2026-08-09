"""Config flow for the Meter-Bus integration.

Asks how the M-Bus master is reached — a local serial port, or a raw TCP bridge
in front of one — checks that something answers, then scans the bus so nobody
has to type a sixteen-digit secondary address.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

# How often the progress dialog is re-rendered while the scan runs.
_PROGRESS_REFRESH_INTERVAL = 3.0

from .api import (
    DEFAULT_BAUD,
    DEFAULT_SERIAL_PORT,
    DEFAULT_TCP_PORT,
    MeterBus,
    MeterBusError,
    parse_addresses,
)
from .const import (
    BAUD_RATES,
    CONF_BAUD,
    CONF_METERS,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)


class MeterBusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meter-Bus."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._scan_task: asyncio.Task[list[str]] | None = None
        self._meters: list[str] = []
        self._scan_error: str | None = None
        # Live scan progress, shown in the progress dialog.
        self._found = 0
        self._prefix = ""
        self._last_refresh = 0.0

    # -- how the master is connected -------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the M-Bus master is connected."""
        return self.async_show_menu(step_id="user", menu_options=["serial", "tcp"])

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a master on a serial port of this machine."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_SERIAL_PORT: user_input[CONF_SERIAL_PORT],
                CONF_BAUD: user_input[CONF_BAUD],
            }
            result = await self._async_check(data, user_input[CONF_SERIAL_PORT], errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="serial",
            data_schema=self._serial_schema(user_input),
            errors=errors,
        )

    async def async_step_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a master behind a raw TCP bridge (ser2net, socat)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
            }
            unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
            result = await self._async_check(data, unique_id, errors)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="tcp", data_schema=self._tcp_schema(user_input), errors=errors
        )

    async def _async_check(
        self, data: dict[str, Any], unique_id: str, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Probe the link, then move on to the scan.

        Returns ``None`` when the probe failed, with ``errors`` filled in, so
        the caller re-shows its own form. Probing first matters: the scan takes
        minutes, and a wrong port or wrong parity should fail in a second
        rather than after a full sweep of silence.
        """
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        bus = _bus_from(data)
        try:
            await self.hass.async_add_executor_job(bus.probe)
        except (MeterBusError, OSError) as err:
            LOGGER.debug("M-Bus probe failed: %s", err)
            errors["base"] = "cannot_connect"
            return None
        finally:
            await self.hass.async_add_executor_job(bus.close)

        self._data = data
        return await self.async_step_scan()

    # -- scanning --------------------------------------------------------

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run the wildcard scan, showing progress while it works.

        A bus of forty meters takes around six minutes at 2400 baud. That is
        far too long to block a form on, hence the progress step: the dialog
        stays alive and the user can see it is still working.
        """
        task = self._scan_task
        if task is None:
            task = self._scan_task = self.hass.async_create_task(self._async_scan())

        if not task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scanning",
                progress_task=task,
                description_placeholders={
                    "found": str(self._found),
                    "prefix": self._prefix or "…",
                },
            )

        return self.async_show_progress_done(next_step_id="meters")

    async def _async_scan(self) -> list[str]:
        """Scan the bus in the executor, recording any failure for the caller."""
        bus, ours = self._scan_bus()
        try:
            self._meters = await self.hass.async_add_executor_job(
                partial(bus.scan, on_progress=self._on_scan_progress)
            )
        except (MeterBusError, OSError) as err:
            LOGGER.debug("M-Bus scan failed: %s", err)
            self._scan_error = "cannot_connect"
        finally:
            if ours:
                await self.hass.async_add_executor_job(bus.close)
        return self._meters

    def _scan_bus(self) -> tuple[MeterBus, bool]:
        """Return the bus to scan on, and whether we are the one to close it.

        A reconfigure runs while the entry is loaded and its coordinator is
        already polling — which means it already holds the port. Opening a
        second one is not merely wasteful, it does not work: a serial device
        opened twice gets two masters garbling each other, and a serial-to-TCP
        bridge is typically single-client, so the second connection is never
        served and every probe times out. The scan then reads the entire bus as
        empty and "finds" nothing.

        So reuse the live bus. Its lock makes the sweep and the scan take turns
        one transaction at a time instead of interleaving mid-conversation.
        """
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            coordinator = getattr(entry, "runtime_data", None)
            if entry.state is ConfigEntryState.LOADED and coordinator is not None:
                return coordinator.bus, False
        return _bus_from(self._data), True

    def _on_scan_progress(self, found: int, prefix: str) -> None:
        """Note progress and re-render the dialog. Runs on the executor thread.

        Home Assistant's progress step shows an indeterminate spinner, not a
        bar, so "progress" here is the text inside it. Re-rendering means
        re-entering this step, which is what ``async_configure`` does — the step
        then returns ``async_show_progress`` again with fresh placeholders.

        Two facts, both always true: how many meters exist so far, and which
        address prefix is being tried right now. Deliberately no percentage —
        see :meth:`MeterBus.scan` for why the only one available would lie.
        """
        self._found = found
        self._prefix = prefix
        self.hass.loop.call_soon_threadsafe(self._async_refresh_progress)

    @callback
    def _async_refresh_progress(self) -> None:
        """Ask the frontend to re-render the progress step."""
        now = self.hass.loop.time()
        # The scan reports after every top-level digit and reconfiguring a flow
        # is not free, so throttle: a few seconds is as often as anyone reads it.
        if now - self._last_refresh < _PROGRESS_REFRESH_INTERVAL:
            return
        self._last_refresh = now
        self.hass.async_create_task(self._async_configure_self())

    async def _async_configure_self(self) -> None:
        """Re-enter the current step, ignoring a flow the user has abandoned."""
        try:
            await self.hass.config_entries.flow.async_configure(flow_id=self.flow_id)
        except UnknownFlow:
            pass

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what the scan found and let it be corrected before saving.

        The scan is reliable (see :meth:`MeterBus._probe`), so this is not a
        workaround — it is the escape hatch. A bus that turns up something the
        search does not handle should cost the user one paste, not leave them
        with an integration that cannot see their meters.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            meters, bad = parse_addresses(user_input[CONF_METERS])
            if bad:
                errors[CONF_METERS] = "bad_address"
            elif not meters:
                errors[CONF_METERS] = "no_meters"
            else:
                self._meters = meters
                return await self._async_save()

        default = "\n".join(self._meters)
        return self.async_show_form(
            step_id="meters",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METERS, default=default): TextSelector(
                        TextSelectorConfig(multiline=True)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "count": str(len(self._meters)),
                "scan_note": (
                    "The scan could not run, so nothing was filled in."
                    if self._scan_error
                    else ""
                ),
            },
        )

    async def _async_save(self) -> ConfigFlowResult:
        """Create or update the entry with the confirmed meter list."""
        if self.source == SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            # Union, never replace. A meter that did not answer during this
            # particular scan is far more likely to be briefly unreachable than
            # genuinely gone, and dropping it would take its history and its
            # place in every automation with it. Delete its device to be rid of
            # a meter for good.
            merged = sorted(set(entry.data.get(CONF_METERS, [])) | set(self._meters))
            return self.async_update_reload_and_abort(
                entry,
                title=_title(self._data),
                data_updates={**self._data, CONF_METERS: merged},
            )

        return self.async_create_entry(
            title=_title(self._data),
            data={**self._data, CONF_METERS: sorted(self._meters)},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rescan an already-configured bus to pick up new meters."""
        entry = self._get_reconfigure_entry()
        self._data = {
            key: value for key, value in entry.data.items() if key != CONF_METERS
        }
        return await self.async_step_scan()

    # -- schemas ---------------------------------------------------------

    @staticmethod
    def _serial_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
        """Schema for the serial port form."""
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_SERIAL_PORT,
                    default=defaults.get(CONF_SERIAL_PORT, DEFAULT_SERIAL_PORT),
                ): str,
                vol.Required(
                    CONF_BAUD, default=defaults.get(CONF_BAUD, DEFAULT_BAUD)
                ): vol.In(BAUD_RATES),
            }
        )

    @staticmethod
    def _tcp_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
        """Schema for the TCP bridge form."""
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_TCP_PORT)
                ): vol.Coerce(int),
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> MeterBusOptionsFlow:
        """Return the options flow."""
        return MeterBusOptionsFlow()


class MeterBusOptionsFlow(OptionsFlow):
    """Tune how hard the integration drives the bus."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the poll interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )


def _title(data: dict[str, Any]) -> str:
    """Name the entry after the bus it talks to.

    Not after how many meters it found. A count goes stale the moment a meter
    is added — which is how an entry that had just discovered 41 of them stayed
    titled "M-Bus (9 meters)" — and Home Assistant already shows the device and
    entity counts on the integration card. What the card cannot show, and what
    actually distinguishes one entry from another, is which bus it is.
    """
    if CONF_SERIAL_PORT in data:
        return f"M-Bus ({data[CONF_SERIAL_PORT]})"
    return f"M-Bus ({data[CONF_HOST]}:{data[CONF_PORT]})"


def _bus_from(data: dict[str, Any]) -> MeterBus:
    """Build a client from stored connection settings."""
    return MeterBus(
        serial_port=data.get(CONF_SERIAL_PORT),
        host=data.get(CONF_HOST),
        tcp_port=data.get(CONF_PORT, DEFAULT_TCP_PORT),
        baud=data.get(CONF_BAUD, DEFAULT_BAUD),
    )
