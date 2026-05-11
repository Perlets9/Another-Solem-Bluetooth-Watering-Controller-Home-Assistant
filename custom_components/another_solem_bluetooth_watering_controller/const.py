"""Constants for Another Solem Bluetooth Watering Controller."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "another_solem_bluetooth_watering_controller"

SERVICE_UUID = "108b0001-eab5-bc09-d0ea-0b8f467ce8ee"
WRITE_UUID = "108b0002-eab5-bc09-d0ea-0b8f467ce8ee"
NOTIFY_UUID = "108b0003-eab5-bc09-d0ea-0b8f467ce8ee"

DEVICE_NAME_PREFIXES = ("BL1IP", "BL2IP", "BL4IP", "BL6IP", "BLIP")

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_STATION_COUNT = "station_count"
CONF_DEFAULT_DURATION = "default_duration"
CONF_POLLING_ENABLED = "polling_enabled"
CONF_POLL_INTERVAL = "poll_interval"
CONF_BLUETOOTH_TIMEOUT = "bluetooth_timeout"

SUPPORTED_STATION_COUNTS = (1, 2, 4, 6)
DEFAULT_DURATION = 10
DEFAULT_POLLING_ENABLED = True
DEFAULT_POLL_INTERVAL = 30
DEFAULT_BLUETOOTH_TIMEOUT = 15

MIN_DURATION = 1
MAX_DURATION = 720
MIN_POLL_INTERVAL = 10
MIN_BLUETOOTH_TIMEOUT = 5

DEFAULT_UPDATE_INTERVAL = timedelta(seconds=DEFAULT_POLL_INTERVAL)
