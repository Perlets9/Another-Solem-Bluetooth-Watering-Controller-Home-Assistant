"""Tests for SOLEM BL-IP switch state handling."""

from types import SimpleNamespace

from custom_components.another_solem_bluetooth_watering_controller.const import CONF_NAME
from custom_components.another_solem_bluetooth_watering_controller.protocol import (
    SolemMode,
    SolemStatus,
)
from custom_components.another_solem_bluetooth_watering_controller.switch import StationSwitch


def _coordinator(active_station: int | None):
    return SimpleNamespace(
        address="AA:BB:CC",
        data=SolemStatus(SolemMode.SINGLE_STATION_ACTIVE, True, 300, "raw"),
        active_station=active_station,
        entry=SimpleNamespace(data={CONF_NAME: "SOLEM BL-IP"}),
    )


def test_single_station_status_only_turns_on_matching_station_switch() -> None:
    coordinator = _coordinator(active_station=1)

    assert StationSwitch(coordinator, 1).is_on is True
    assert StationSwitch(coordinator, 2).is_on is False


def test_external_single_station_status_does_not_turn_on_every_station_switch() -> None:
    coordinator = _coordinator(active_station=None)

    assert StationSwitch(coordinator, 1).is_on is False
    assert StationSwitch(coordinator, 2).is_on is False
