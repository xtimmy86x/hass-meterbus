"""The Meter-Bus integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import DEFAULT_BAUD, DEFAULT_TCP_PORT, MeterBus
from .const import (
    CONF_BAUD,
    CONF_METERS,
    CONF_SCAN_INTERVAL,
    CONF_SERIAL_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODEL_HUB,
)
from .coordinator import MeterBusConfigEntry, MeterBusCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: MeterBusConfigEntry) -> bool:
    """Set up Meter-Bus from a config entry."""
    bus = MeterBus(
        serial_port=entry.data.get(CONF_SERIAL_PORT),
        host=entry.data.get(CONF_HOST),
        tcp_port=entry.data.get(CONF_PORT, DEFAULT_TCP_PORT),
        baud=entry.data.get(CONF_BAUD, DEFAULT_BAUD),
    )

    # The meter list comes from the scan the config flow ran. It is not
    # refreshed here: a full wildcard scan takes minutes of bus time, which is
    # not something to spend on every Home Assistant restart. Reconfigure the
    # entry to rescan after adding a meter.
    meters: list[str] = list(entry.data.get(CONF_METERS, []))
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinator = MeterBusCoordinator(hass, entry, bus, meters, scan_interval)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_register_hub_device(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_register_hub_device(hass: HomeAssistant, entry: MeterBusConfigEntry) -> None:
    """Register the bus master itself, so the meters hang off it in the UI."""
    if CONF_SERIAL_PORT in entry.data:
        connection = entry.data[CONF_SERIAL_PORT]
    else:
        connection = f"{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}"
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="M-Bus",
        model=MODEL_HUB,
        serial_number=connection,
    )


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options change (the poll interval is set at construction)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MeterBusConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # A TCP bridge is usually single-client, so holding the socket after
        # unload would lock out the next setup — and a serial port held open
        # locks out everything else on the machine.
        await hass.async_add_executor_job(entry.runtime_data.bus.close)
    return unload_ok
