"""Sensor platform: every mapped data record of every meter.

One generic entity class covers water, heat and electricity meters alike. What
differs between them is data, not behaviour, and that data lives in
:mod:`.profiles`.
"""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MeterBusConfigEntry, MeterBusCoordinator
from .entity import MeterBusEntity
from .profiles import SensorPoint, sensors_for, values_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterBusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every meter that has answered so far."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new() -> None:
        """Create entities for meters and records not seen yet.

        Entities come from what the meters actually reported, so a meter that
        was offline during startup — or one that only reveals a record on some
        replies — gets its entities as soon as it does answer, instead of
        needing a reload. Nothing is ever removed here: a meter that stops
        answering goes unavailable and keeps its history.
        """
        new: list[MeterBusSensor] = []
        for address, reading in (coordinator.data or {}).items():
            for point in sensors_for(reading):
                unique_id = f"{address}_{point.key}"
                if unique_id in known:
                    continue
                known.add(unique_id)
                new.append(MeterBusSensor(coordinator, address, point))
        if new:
            async_add_entities(new)

    _async_add_new()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new))


class MeterBusSensor(MeterBusEntity, SensorEntity):
    """One data record of one meter."""

    def __init__(
        self, coordinator: MeterBusCoordinator, address: str, point: SensorPoint
    ) -> None:
        """Initialize the sensor from the record that produced it."""
        super().__init__(coordinator, address, point.key)
        self._key = point.key
        sensor = point.sensor
        # Names come from strings.json and icons from icons.json, both keyed on
        # translation_key, so neither is hardcoded here and both are
        # translatable. The tariff and billing-period numbers ride along as
        # placeholders rather than being formatted into a name.
        self._attr_translation_key = point.translation_key
        self._attr_translation_placeholders = point.placeholders
        self.entity_description = SensorEntityDescription(
            key=point.key,
            native_unit_of_measurement=sensor.unit,
            # Home Assistant's device and state classes are string enums whose
            # values are exactly what profiles.py stores, so this is a cast and
            # not a translation table.
            device_class=(
                SensorDeviceClass(sensor.device_class) if sensor.device_class else None
            ),
            state_class=(
                SensorStateClass(sensor.state_class) if sensor.state_class else None
            ),
            suggested_display_precision=sensor.precision,
            entity_category=EntityCategory.DIAGNOSTIC if sensor.diagnostic else None,
            entity_registry_enabled_default=sensor.enabled,
        )

    @property
    def native_value(self) -> float | None:
        """Return this record's value from the last successful read."""
        reading = self.reading
        if reading is None:
            return None
        return values_for(reading).get(self._key)
