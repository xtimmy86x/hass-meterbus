"""Poll every meter on one M-Bus and hand their readings to entities."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MeterBus, MeterBusError, MeterReading
from .const import DOMAIN, LOGGER

type MeterBusConfigEntry = ConfigEntry[MeterBusCoordinator]


class MeterBusCoordinator(DataUpdateCoordinator[dict[str, MeterReading]]):
    """Sweep the bus meter by meter, on one shared master."""

    config_entry: MeterBusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MeterBusConfigEntry,
        bus: MeterBus,
        meters: list[str],
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.bus = bus
        self.meters = meters

    async def _async_update_data(self) -> dict[str, MeterReading]:
        """Read every configured meter once.

        The whole sweep runs in one executor job rather than one per meter: the
        bus is half-duplex and a single master, so handing the reads to the
        thread pool separately would buy nothing and risk interleaving them.

        Nothing is accumulated here. A failed write or a meter that did not
        answer is simply absent from the next result — the coordinator already
        keeps the last good data for everything else, which is what stops a
        long outage from growing a buffer until the process is killed.
        """
        try:
            data = await self.hass.async_add_executor_job(
                self.bus.read_meters, self.meters
            )
        except (MeterBusError, OSError) as err:
            raise UpdateFailed(f"M-Bus sweep failed: {err}") from err

        if not data:
            # Not one meter answered, so this is the bus or the master, not the
            # meters. Failing the update marks everything unavailable at once
            # instead of leaving stale readings on show.
            raise UpdateFailed("no meter answered on the bus")

        missing = [address for address in self.meters if address not in data]
        if missing:
            LOGGER.debug(
                "M-Bus sweep: %d of %d meters did not answer (%s)",
                len(missing),
                len(self.meters),
                ", ".join(missing),
            )
        return data
