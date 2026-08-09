"""Constants for the Meter-Bus integration."""

from __future__ import annotations

import logging

DOMAIN = "meterbus"
LOGGER = logging.getLogger(__package__)

MODEL_HUB = "M-Bus master"

CONF_SERIAL_PORT = "serial_port"
CONF_BAUD = "baud"
CONF_METERS = "meters"
CONF_SCAN_INTERVAL = "scan_interval"

# One read is a select plus a request plus the reply — measured at about 0.7 s
# per meter at 2400 baud, so a 41-meter bus sweeps in around 30 seconds. The
# bus is half-duplex, so that time is serial and cannot be shortened by reading
# meters in parallel. Five minutes leaves the bus idle 90% of the time; these
# are cumulative counters that move in hours, so there is nothing to gain from
# polling harder.
DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 60
MAX_SCAN_INTERVAL = 86400

BAUD_RATES = [300, 600, 1200, 2400, 4800, 9600, 19200, 38400]
