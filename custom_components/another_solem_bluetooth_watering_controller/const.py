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
# Legacy single poll interval; kept for backward compatibility with existing
# config entries. New deployments use the adaptive idle/active pair below.
CONF_POLL_INTERVAL = "poll_interval"
CONF_IDLE_POLL_INTERVAL = "idle_poll_interval"
CONF_ACTIVE_POLL_INTERVAL = "active_poll_interval"
CONF_BLUETOOTH_TIMEOUT = "bluetooth_timeout"
CONF_KEEP_CONNECTION = "keep_connection"
CONF_CONNECTION_IDLE_TIMEOUT = "connection_idle_timeout"

SUPPORTED_STATION_COUNTS = (1, 2, 4, 6)
DEFAULT_DURATION = 10
DEFAULT_POLLING_ENABLED = True
# Legacy default; only used when migrating old config entries.
DEFAULT_POLL_INTERVAL = 30
# Adaptive polling defaults: poll often when actively watering, sparsely when
# the controller is idle to save BLE bandwidth and device battery.
DEFAULT_IDLE_POLL_INTERVAL = 600
DEFAULT_ACTIVE_POLL_INTERVAL = 30
DEFAULT_BLUETOOTH_TIMEOUT = 15
DEFAULT_KEEP_CONNECTION = True
# Short keepalive: long enough to share a connection across back-to-back
# user actions (e.g. tap switch then check sensor), short enough that idle
# polling does not loiter on the BL-IP's radio. Keeping a battery-powered
# peripheral in "connected" state is far more expensive than reconnecting
# with bleak-retry-connector's service cache, so we err on the short side.
DEFAULT_CONNECTION_IDLE_TIMEOUT = 15

MIN_DURATION = 1
MAX_DURATION = 720
MIN_POLL_INTERVAL = 10
MIN_IDLE_POLL_INTERVAL = 30
MIN_ACTIVE_POLL_INTERVAL = 10
MIN_BLUETOOTH_TIMEOUT = 5
MIN_CONNECTION_IDLE_TIMEOUT = 0

DEFAULT_UPDATE_INTERVAL = timedelta(seconds=DEFAULT_IDLE_POLL_INTERVAL)
