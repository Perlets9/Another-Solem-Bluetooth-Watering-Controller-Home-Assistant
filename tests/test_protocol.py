"""Tests for the SOLEM BL-IP protocol helpers."""

import pytest

from custom_components.another_solem_bluetooth_watering_controller.protocol import (
    COMMIT_COMMAND,
    STATUS_COMMAND,
    SolemMode,
    build_all_stations_command,
    build_station_command,
    parse_status_notification,
    stop_command,
)


def test_build_station_command() -> None:
    assert build_station_command(1, 5).hex() == "3105120100012c"


def test_build_station_6_command() -> None:
    assert build_station_command(6, 5).hex() == "3105120600012c"


def test_build_all_stations_command() -> None:
    assert build_all_stations_command(5).hex() == "3105110000012c"


def test_stop_and_status_commands() -> None:
    assert stop_command().hex() == "31051500ff0000"
    assert STATUS_COMMAND.hex() == "3105a000010000"
    assert COMMIT_COMMAND.hex() == "3b00"


@pytest.mark.parametrize("minutes", [0, 721])
def test_duration_bounds(minutes: int) -> None:
    with pytest.raises(ValueError):
        build_station_command(1, minutes)


@pytest.mark.parametrize("station", [0, 7])
def test_station_bounds(station: int) -> None:
    with pytest.raises(ValueError):
        build_station_command(station, 5)


def test_parse_single_station_status() -> None:
    payload = bytes.fromhex("32100242000000000000000000012c000000")
    status = parse_status_notification(payload)
    assert status.mode is SolemMode.SINGLE_STATION_ACTIVE
    assert status.active is True
    assert status.timer_remaining == 300
    assert status.raw == payload.hex()


def test_parse_idle_status() -> None:
    payload = bytes.fromhex("321002400000000000000000000000000000")
    status = parse_status_notification(payload)
    assert status.mode is SolemMode.IDLE
    assert status.active is False
    assert status.timer_remaining == 0


def test_parse_ignores_non_status_packet() -> None:
    payload = bytes.fromhex("321001400000000000000000000000000000")
    status = parse_status_notification(payload)
    assert status.mode is SolemMode.UNKNOWN
    assert status.active is False
