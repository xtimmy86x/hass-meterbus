"""Which M-Bus data records become Home Assistant sensors, and as what.

This is the whole "many kinds of meter" problem, and it is a lookup table
rather than a class hierarchy — because M-Bus payloads are self-describing.
A water meter from Apator, a Siemens heat meter and a Siemens electricity
meter all answer with the same EN 13757-3 record structure; only which records
they carry differs. So the table is keyed on what the record *says it is*
(VIF unit and measure unit), never on a per-brand device type. Supporting a new
meter is adding rows here, not a new module, platform or repository.

Records with no row are skipped. That is what silently drops fabrication
numbers, model strings, parameter-set identifiers, customer locations and
manufacturer-specific blobs without a single special case.

ponytail: device and state classes are HA's string enum *values*, so this
module imports no Home Assistant and stays testable against captured frames
with nothing installed but pyMeterBus. ``sensor.py`` feeds them straight back
through ``SensorDeviceClass(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .api import MeterReading, Record

# Only instantaneous readings become sensors by default.
#
# The Siemens electricity meters here also report MAXIMUM_VALUE and
# MINIMUM_VALUE variants of every energy register — nine records where three
# carry information. On this hardware the maxima read identical to the totals
# and the minima are all zero, so they are noise, not demand data. Skipping by
# function drops eighteen dead entities across nine meters.
FUNCTION_INSTANTANEOUS = "FunctionType.INSTANTANEOUS_VALUE"

# Wh -> kWh, and J -> kWh. The meters report "100 Wh" and "10 Wh" units on the
# wire; pyMeterBus has already applied that exponent, so what arrives here is
# plain Wh and only the unit change is left.
_WH_TO_KWH = 1e-3
_J_TO_KWH = 1 / 3.6e6


@dataclass(frozen=True)
class Sensor:
    """How one kind of record is presented in Home Assistant."""

    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    scale: float = 1.0
    precision: int | None = None
    diagnostic: bool = False
    enabled: bool = True
    icon: str | None = None


# Media whose volume is a circulating loop rather than something consumed:
# heat (in and out), cooling load, and combined heat/cooling meters.
_CIRCULATING_MEDIA = frozenset({0x04, 0x0A, 0x0B, 0x0C, 0x0D})


# Keyed on (VIF unit, measure unit) as pyMeterBus names them. Both halves are
# needed: ENERGY_WH and ENERGY_J are the same quantity in different units, and
# keying on the VIF alone would silently mis-scale one of them.
SENSORS: dict[tuple[str, str], Sensor] = {
    ("VIFUnit.ENERGY_WH", "MeasureUnit.WH"): Sensor(
        key="energy",
        name="Energy",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        scale=_WH_TO_KWH,
        precision=2,
    ),
    ("VIFUnit.ENERGY_J", "MeasureUnit.J"): Sensor(
        key="energy",
        name="Energy",
        unit="kWh",
        device_class="energy",
        state_class="total_increasing",
        scale=_J_TO_KWH,
        precision=2,
    ),
    ("VIFUnit.VOLUME", "MeasureUnit.M3"): Sensor(
        key="volume",
        name="Volume",
        unit="m³",
        device_class="water",
        state_class="total_increasing",
        precision=3,
    ),
    ("VIFUnit.VOLUME_FLOW", "MeasureUnit.M3_H"): Sensor(
        key="volume_flow",
        name="Flow",
        unit="m³/h",
        device_class="volume_flow_rate",
        state_class="measurement",
        precision=3,
    ),
    ("VIFUnit.POWER_W", "MeasureUnit.W"): Sensor(
        key="power",
        name="Power",
        unit="W",
        device_class="power",
        state_class="measurement",
        precision=0,
    ),
    ("VIFUnit.FLOW_TEMPERATURE", "MeasureUnit.C"): Sensor(
        key="flow_temperature",
        name="Flow temperature",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        precision=1,
    ),
    ("VIFUnit.RETURN_TEMPERATURE", "MeasureUnit.C"): Sensor(
        key="return_temperature",
        name="Return temperature",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        precision=1,
    ),
    ("VIFUnit.EXTERNAL_TEMPERATURE", "MeasureUnit.C"): Sensor(
        key="external_temperature",
        name="External temperature",
        unit="°C",
        device_class="temperature",
        state_class="measurement",
        precision=1,
    ),
    ("VIFUnit.TEMPERATURE_DIFFERENCE", "MeasureUnit.K"): Sensor(
        key="temperature_difference",
        name="Temperature difference",
        unit="K",
        device_class="temperature",
        state_class="measurement",
        precision=1,
    ),
    ("VIFUnit.MASS", "MeasureUnit.KG"): Sensor(
        key="mass",
        name="Mass",
        unit="kg",
        device_class="weight",
        state_class="total_increasing",
        precision=2,
    ),
    # Runtime counters are housekeeping, not something anyone puts on a
    # dashboard, so they land under Diagnostic. pyMeterBus normalises the
    # meter's "days" or "hours" to seconds, which is what DURATION wants.
    ("VIFUnit.OPERATING_TIME", "MeasureUnit.SECONDS"): Sensor(
        key="operating_time",
        name="Operating time",
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        precision=0,
        diagnostic=True,
    ),
    ("VIFUnit.ON_TIME", "MeasureUnit.SECONDS"): Sensor(
        key="on_time",
        name="On time",
        unit="s",
        device_class="duration",
        state_class="total_increasing",
        precision=0,
        diagnostic=True,
    ),
    # A raw bit field whose meaning is manufacturer-specific — useful when a
    # meter misbehaves, meaningless the rest of the time, so it ships disabled.
    ("VIFUnitExt.ERROR_FLAGS", "MeasureUnit.NONE"): Sensor(
        key="error_flags",
        name="Error flags",
        diagnostic=True,
        enabled=False,
        # No device class to draw an icon from, and "0" with no icon reads as
        # nothing at all.
        icon="mdi:alert",
    ),
}


@dataclass(frozen=True)
class SensorPoint:
    """One record matched to its presentation, ready to become an entity."""

    key: str
    name: str
    sensor: Sensor
    value: float | None

    @property
    def unit(self) -> str | None:
        """Unit to report the value in."""
        return self.sensor.unit

    @property
    def enabled(self) -> bool:
        """Whether this entity is created enabled."""
        return self.sensor.enabled


def _decorate(sensor: Sensor, storage: int, tariff: int | None) -> tuple[str, str, bool]:
    """Return the (key, name, enabled) for one record's storage/tariff variant.

    Storage and tariff are not separate kinds of sensor, they are qualifiers on
    the same one — which is why they decorate a row rather than needing rows of
    their own. Nine Siemens energy registers collapse to one table entry.
    """
    key, name, enabled = sensor.key, sensor.name, sensor.enabled
    if tariff:
        key = f"{key}_t{tariff}"
        name = f"{name} tariff {tariff}"
    if storage:
        # Storage > 0 is a billing-date snapshot the meter froze, not a live
        # counter: it steps *backwards* every billing period, which would make
        # a TOTAL_INCREASING sensor report a huge phantom delta to the energy
        # dashboard. Ship it without a state class, and disabled.
        key = f"{key}_prev{storage}"
        name = f"{name} previous period {storage}" if storage > 1 else f"{name} previous period"
        enabled = False
    return key, name, enabled


def sensors_for(reading: MeterReading) -> list[SensorPoint]:
    """Return every sensor a meter's reply supports, in reply order."""
    points: list[SensorPoint] = []
    seen: set[str] = set()
    for record in reading.records:
        point = _point_for(record, reading.medium)
        if point is None or point.key in seen:
            # A meter repeating the same (record, storage, tariff) triple would
            # otherwise collide on unique_id and lose one of the two silently.
            continue
        seen.add(point.key)
        points.append(point)
    return points


def values_for(reading: MeterReading) -> dict[str, float]:
    """Return ``{sensor key: value}`` for one meter's reply."""
    return {
        point.key: point.value
        for point in sensors_for(reading)
        if point.value is not None
    }


def _point_for(record: Record, medium: int) -> SensorPoint | None:
    """Match one record to its presentation, or None if it is not exposed."""
    if record.function != FUNCTION_INSTANTANEOUS:
        return None
    sensor = SENSORS.get((record.vif, record.unit))
    if sensor is None:
        return None
    sensor = _for_medium(sensor, medium)
    value = _numeric(record.value)
    if value is None:
        return None
    key, name, enabled = _decorate(sensor, record.storage, record.tariff)
    if record.storage:
        sensor = replace(sensor, enabled=enabled, state_class=None)
    elif enabled != sensor.enabled:
        sensor = replace(sensor, enabled=enabled)
    return SensorPoint(key=key, name=name, sensor=sensor, value=value * sensor.scale)


def _for_medium(sensor: Sensor, medium: int) -> Sensor:
    """Adjust a row for what the meter actually measures.

    The record says "volume in m³" whether the meter is a water meter or a heat
    meter, but they do not mean the same thing. Water through a water meter is
    consumed and gone; water through a calorimeter is a heating loop going
    round, and it comes back. Presented as ``device_class: water`` the latter
    becomes eligible for the Energy dashboard's Water section, where it would
    inflate household water use by the entire circulation. Home Assistant's own
    ``landisgyr_heat_meter`` models the same figure as VOLUME, which is a
    quantity rather than a consumption, and that is the right call.

    Energy has the mirror problem: thermal and electrical kWh are both
    ``device_class: energy`` and draw the same icon, so in a list of forty-odd
    meters there is nothing to tell a calorimeter from an electricity meter.
    An icon is the cheapest thing that does.
    """
    if sensor.key == "volume" and medium in _CIRCULATING_MEDIA:
        return replace(sensor, device_class="volume", icon="mdi:pipe")
    if sensor.key == "energy" and medium in _CIRCULATING_MEDIA:
        return replace(sensor, icon="mdi:fire")
    return sensor


def _numeric(value: object) -> float | None:
    """Return ``value`` as a float, or None if it is not a number.

    Dates, timestamps and ASCII fields (a meter's parameter-set id, say) reach
    here whenever they share a VIF row with numbers; they are not sensors.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        return float(value)  # Decimal and int both convert exactly enough
    except (TypeError, ValueError):
        return None
