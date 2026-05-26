"""Tests for the SOLEM BL-IP protocol helpers."""

import pytest

from custom_components.another_solem_bluetooth_watering_controller.protocol import (
    COMMIT_COMMAND,
    STATUS_COMMAND,
    SolemMode,
    build_all_stations_command,
    build_run_program_command,
    build_station_command,
    parse_device_info,
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
    # STATUS_COMMAND was simplified to the bare `3b 00` frame (matches the
    # MySolem app on the wire). It happens to be identical to COMMIT_COMMAND
    # because the controller's status request and commit frame are the same.
    assert STATUS_COMMAND.hex() == "3b00"
    assert COMMIT_COMMAND.hex() == "3b00"


@pytest.mark.parametrize("minutes", [0, 721])
def test_duration_bounds(minutes: int) -> None:
    with pytest.raises(ValueError):
        build_station_command(1, minutes)


@pytest.mark.parametrize("station", [0, 7])
def test_station_bounds(station: int) -> None:
    with pytest.raises(ValueError):
        build_station_command(station, 5)


@pytest.mark.parametrize(
    "program,expected_hex",
    # On-the-wire layout: `31 05 14 00 0N 00 00` (program N at byte 4,
    # BE16). Matches the canonical SOLEM reference implementation
    # `hacking/solem_bleak.py` -- packing program at byte 3 makes the
    # firmware silently drop the frame.
    [(1, "31051400010000"), (2, "31051400020000"), (3, "31051400030000")],
)
def test_build_run_program_command(program: int, expected_hex: str) -> None:
    assert build_run_program_command(program).hex() == expected_hex


@pytest.mark.parametrize("program", [0, 4, -1])
def test_run_program_bounds(program: int) -> None:
    with pytest.raises(ValueError):
        build_run_program_command(program)


def test_parse_single_station_status() -> None:
    payload = bytes.fromhex("32100242000000000000000000012c000000")
    status = parse_status_notification(payload)
    assert status.mode is SolemMode.SINGLE_STATION_ACTIVE
    assert status.active is True
    assert status.timer_remaining == 300
    assert status.raw == payload.hex()


def test_parse_bl2ip_single_station_status_timer_from_trailing_position() -> None:
    payload = bytes.fromhex("3210024200aaaaaa00024e131000001004aa")
    status = parse_status_notification(payload)
    assert status.mode is SolemMode.SINGLE_STATION_ACTIVE
    assert status.active is True
    assert status.timer_remaining == 1194


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


def test_parse_battery_level_from_real_capture() -> None:
    # Modeled on a real MySolem snoop: byte 10 = 0x4d (77%) on a
    # partially discharged controller.
    payload = bytes.fromhex("321002400000000000004d17000000")
    status = parse_status_notification(payload)
    assert status.battery_level == 77


def test_parse_battery_level_none_when_byte_zero() -> None:
    # Synthetic payloads (and freshly-booted controllers reporting 0) are
    # treated as "unknown" rather than misleadingly reporting 0%.
    payload = bytes.fromhex("321002400000000000000000000000000000")
    status = parse_status_notification(payload)
    assert status.battery_level is None


def test_parse_battery_level_none_when_out_of_range() -> None:
    # Any value outside 1..100 is considered not a percentage.
    payload = bytes.fromhex("321002400000000000008000000000")
    status = parse_status_notification(payload)
    assert status.battery_level is None


def test_parse_device_info_from_real_capture() -> None:
    # Record 0x01 payload (after stripping `[10] [LEN] [REC_IDX]`):
    #   MAC=C8:B9:61:D5:AA:7E, firmware bytes 5.1.7 at offsets 9..11.
    record_01 = bytes.fromhex("c8b961d5aa7ee202410501070100")
    # Record 0x00 payload: 15-byte ASCII name null-padded.
    record_00 = bytes.fromhex("424c3249502d443541413745000000")

    info = parse_device_info({0x01: record_01, 0x00: record_00})

    assert info.mac == "C8:B9:61:D5:AA:7E"
    assert info.device_name == "BL2IP-D5AA7E"
    assert info.model == "BL2IP"
    assert info.firmware == "5.1.7"


def test_parse_device_info_handles_missing_records() -> None:
    info = parse_device_info({})
    assert info.mac == ""
    assert info.device_name == ""
    assert info.model is None
    assert info.firmware is None


def test_parse_device_info_skips_firmware_when_bytes_implausible() -> None:
    # Firmware bytes all zero -> implausible, must stay None
    record_01 = bytes.fromhex("c8b961d5aa7ee2024100000000000000")
    info = parse_device_info({0x01: record_01})
    assert info.mac == "C8:B9:61:D5:AA:7E"
    assert info.firmware is None
