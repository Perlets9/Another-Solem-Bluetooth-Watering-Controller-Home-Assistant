"""Pure SOLEM BL-IP BLE protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import struct

COMMIT_COMMAND = bytes.fromhex("3b00")
# Status / commit frame. Writing `3b 00` triggers a 3-packet status response
# from the controller (same frame MySolem uses). It is also the commit frame
# appended after `31 05 ...` action commands; that double role is intentional
# and matches the controller firmware behavior.
STATUS_COMMAND = bytes.fromhex("3b00")
# Device info read: returns two notifications with opcode 0x10. Record 0x01
# contains MAC + firmware bytes, record 0x00 contains the device name.
DEVICE_INFO_COMMAND = bytes.fromhex("0f00")
# Programs read: returns 84 notifications with opcode 0x3a (12 slots x 7 rows).
PROGRAMS_READ_COMMAND = bytes.fromhex("3900")

MIN_DURATION = 1
MAX_DURATION = 720
MIN_STATION = 1
MAX_STATION = 6
# MySolem exposes 3 programs (A, B, C). The firmware reserves more slots
# but only the first three are user-addressable from the app's "Run program"
# button. We accept the same range here.
MIN_PROGRAM = 1
MAX_PROGRAM = 3


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
    # Experimental: byte 10 of the status packet. Strong candidate for
    # battery level (see reverse engineering notes). Observed values
    # across captures 9 months apart went 88 -> 77, consistent with a 9V
    # battery slowly discharging. Exposed as a sensor but disabled by
    # default until long-term verification.
    battery_level: int | None = None


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


def build_run_program_command(program: int) -> bytes:
    """Build the "run program N on demand" command.

    The on-the-wire payload is ``31 05 14 00 0N 00 00`` where ``N`` is the
    1-indexed program number (1=A, 2=B, 3=C). The same opcode layout is
    documented in ``DISCOVERY.md`` of the reverse-engineering repo and is
    what the working ``hacking/solem_bleak.py`` reference library has been
    sending since the original Solem reverse engineering work (encoded as
    ``struct.pack(">HBHH", 0x3105, 0x14, program, 0x0000)``).

    Earlier versions of this function packed ``program`` into byte 3
    instead of byte 4 -- following a mistranscribed example in
    ``SNOOP-2026-05-25-run2.md`` -- and the controller silently ignored
    the resulting frame. Keep the byte layout exactly as below.
    """
    if program < MIN_PROGRAM or program > MAX_PROGRAM:
        raise ValueError(
            f"Program must be between {MIN_PROGRAM} and {MAX_PROGRAM}"
        )
    return struct.pack(">HBHH", 0x3105, 0x14, program, 0x0000)


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
    battery_level = _parse_battery_level(data)
    return SolemStatus(mode, active, timer_remaining, data.hex(), battery_level)


@dataclass(frozen=True)
class SolemDeviceInfo:
    """Identity information returned by the controller's `0x0f` request.

    Parsed from the two `0x10` records emitted in response. The MAC and the
    advertised device name are reliable across all five captures; the
    firmware bytes are best-effort and may be ``None`` on devices that do
    not embed them in the same position.
    """

    mac: str
    device_name: str
    model: str | None
    firmware: str | None


def parse_device_info(records: dict[int, bytes]) -> SolemDeviceInfo:
    """Parse a `0x10` device-info dump (records indexed by `record_idx`).

    Layout of each record payload (after stripping `[OPCODE] [LEN] [REC_IDX]`):

    Record 0x01 (15 bytes payload after REC_IDX, total 16 incl. opcode header):
        ``[MAC:6] [??:3] [FW_MAJOR:1] [FW_MINOR:1] [FW_PATCH:1] [??:2]``

    Record 0x00 (15 bytes payload after REC_IDX):
        ``[NAME_ASCII:15]`` -- null-terminated, e.g. ``"BL2IP-D5AA7E"``.

    Empirically firmware "5.1.7" maps to bytes ``05 01 07`` in record 1 at
    offsets 9..11 of the payload (see SNOOP-2026-05-25.md §3.1).
    """
    mac_record = records.get(0x01, b"")
    name_record = records.get(0x00, b"")

    if len(mac_record) >= 6:
        mac = ":".join(f"{b:02X}" for b in mac_record[:6])
    else:
        mac = ""

    firmware: str | None = None
    if len(mac_record) >= 12:
        major, minor, patch = mac_record[9], mac_record[10], mac_record[11]
        if 0 < major < 100 and minor < 100 and patch < 100:
            firmware = f"{major}.{minor}.{patch}"

    device_name = name_record.rstrip(b"\x00").decode("ascii", errors="replace")
    model = None
    if device_name:
        # "BL2IP-D5AA7E" -> model "BL2IP"
        if "-" in device_name:
            candidate = device_name.split("-", 1)[0]
        else:
            candidate = device_name
        model = candidate or None

    return SolemDeviceInfo(
        mac=mac, device_name=device_name, model=model, firmware=firmware
    )


def _parse_battery_level(data: bytes) -> int | None:
    """Best-effort extraction of the battery percentage candidate byte.

    The 11th byte of status packet 1 has been observed to drift downward
    over time on the same controller (88 -> 77 over ~9 months), which
    matches a 9V battery slow discharge curve. Treat values outside the
    0-100 range as "unknown" rather than guessing.
    """
    if len(data) < 11:
        return None
    raw = data[10]
    if raw == 0 or raw > 100:
        return None
    return raw
