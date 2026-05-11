"""Tests for SOLEM BL-IP coordinator helpers."""

from datetime import timedelta

from custom_components.another_solem_bluetooth_watering_controller.const import (
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
)
from custom_components.another_solem_bluetooth_watering_controller.coordinator import (
    polling_update_interval,
)


def test_polling_update_interval_uses_configured_interval_when_enabled() -> None:
    assert polling_update_interval({CONF_POLLING_ENABLED: True, CONF_POLL_INTERVAL: 15}) == timedelta(
        seconds=15
    )


def test_polling_update_interval_is_none_when_disabled() -> None:
    assert polling_update_interval({CONF_POLLING_ENABLED: False, CONF_POLL_INTERVAL: 15}) is None
