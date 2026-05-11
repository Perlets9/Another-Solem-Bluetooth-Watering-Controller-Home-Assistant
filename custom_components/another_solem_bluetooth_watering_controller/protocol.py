"""Pure SOLEM BL-IP BLE protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import struct

COMMIT_COMMAND = bytes.fromhex("3b00")
STATUS_COMMAND = bytes.fromhex("3105a000010000")

MIN_DURATION = 1
MAX_DURATION = 720
MIN_STATION = 1
MAX_STATION = 6


class SolemMode(StrEnum):
    """Known SOLEM BL-IP controller modes."""

    IDLE = "idle"
    ALL_STATIONS_ACTIVE = "all_stations_active"
    SINGLE_STATION_ACTIVE = "single_station_active"
    PROGRAMMED_OFF = "programmed_off"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SolemStatus:
    """Parsed status from a SOLEM BL-IP notification."""

    mode: SolemMode
    active: bool
    timer_remaining: int
    raw: str


def _validate_duration(minutes: int) -> None:
    if minutes < MIN_DURATION or minutes > MAX_DURATION:
        raise ValueError(f"Duration must be between {MIN_DURATION} and {MAX_DURATION} minutes")


def build_station_command(station: int, minutes: int) -> bytes:
    """Build a command that waters one station for the given number of minutes."""
    if station < MIN_STATION or station > MAX_STATION:
        raise ValueError(f"Station must be between {MIN_STATION} and {MAX_STATION}")
    _validate_duration(minutes)
    seconds = minutes * 60
    return struct.pack(">HBBBH", 0x3105, 0x12, station, 0x00, seconds)


def build_all_stations_command(minutes: int) -> bytes:
    """Build a command that waters all stations for the given number of minutes."""
    _validate_duration(minutes)
    seconds = minutes * 60
    return struct.pack(">HBHH", 0x3105, 0x11, 0x0000, seconds)


def stop_command() -> bytes:
    """Build the stop manual watering command."""
    return bytes.fromhex("31051500ff0000")


def _parse_timer_remaining(data: bytes) -> int:
    """Parse known BL-IP timer positions from a status packet."""
    candidates = []
    if len(data) >= 15:
        candidates.append(struct.unpack(">H", data[13:15])[0])
    if len(data) >= 18:
        candidates.append(struct.unpack(">H", data[16:18])[0])
    return next((timer for timer in candidates if timer > 0), 0)


def parse_status_notification(data: bytes) -> SolemStatus:
    """Parse the first status notification packet returned by the controller."""
    if len(data) < 15 or data[2] != 0x02:
        return SolemStatus(SolemMode.UNKNOWN, False, 0, data.hex())

    mode = {
        0x40: SolemMode.IDLE,
        0x41: SolemMode.ALL_STATIONS_ACTIVE,
        0x42: SolemMode.SINGLE_STATION_ACTIVE,
        0x02: SolemMode.PROGRAMMED_OFF,
    }.get(data[3], SolemMode.UNKNOWN)

    timer_remaining = _parse_timer_remaining(data)
    active = mode in {SolemMode.ALL_STATIONS_ACTIVE, SolemMode.SINGLE_STATION_ACTIVE}
    return SolemStatus(mode, active, timer_remaining, data.hex())
