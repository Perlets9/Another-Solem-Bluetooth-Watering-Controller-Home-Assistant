"""Tests for the SOLEM BL-IP program parser/encoder.

Frames in this module are reconstructed from the byte-level evidence in
the snoop captures stored in the sibling reverse-engineering repository
(SNOOP-2026-05-25*.md). We only need a handful of slots per test: the
parser tolerates partial dumps (slots with fewer than 7 rows are skipped),
which lets us craft minimal fixtures while still exercising real layouts.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from custom_components.another_solem_bluetooth_watering_controller.programs import (
    FLAGS_ROW_LEN,
    MAX_START_TIMES,
    NAME_ROW_LEN,
    PROGRAM_SLOT_COUNT,
    RESERVED_ROW1_LEN,
    RESERVED_ROW5_LEN,
    START_TIME_SENTINEL,
    START_TIMES_ROW_LEN,
    STATIONS_ROW_LEN,
    TAIL_ROW_LEN,
    FrequencyType,
    Program,
    apply_program_changes,
    encode_program,
    parse_program_dump,
    parse_program_slot,
)


# ---------------------------------------------------------------------- #
# Helpers to build frames
# ---------------------------------------------------------------------- #


def _frame(opcode: int, payload_size: int, seq: int, slot: int, payload: bytes) -> bytes:
    """Pack a single dump frame: ``[OPCODE] [LEN] [SEQ] [SLOT] [PAYLOAD]``.

    The LEN byte counts ``SEQ + SLOT + PAYLOAD`` to match the firmware's
    wire format (see ``programs.WIRE_LEN_OVERHEAD``).
    """
    payload = payload.ljust(payload_size, b"\x00")[:payload_size]
    wire_len = payload_size + 2
    return bytes([opcode, wire_len, seq, slot]) + payload


def _name_payload(name: str) -> bytes:
    return name.encode("ascii").ljust(NAME_ROW_LEN, b"\x00")


def _flags_payload(
    *,
    budget: int = 100,
    freq: int = 0x00,
    dow: int = 0x7F,
    period: int = 0,
    days_to_next: int = 0,
    day: int = 25,
    month: int = 5,
    year: int = 2026,
) -> bytes:
    out = bytearray(FLAGS_ROW_LEN)
    out[3] = budget
    out[4] = freq
    out[5] = dow
    out[6] = period
    out[7] = days_to_next
    out[8] = day
    out[9] = month
    out[10] = (year >> 8) & 0xFF
    out[11] = year & 0xFF
    return bytes(out)


def _start_times_payload(start_minutes: list[int]) -> bytes:
    values = list(start_minutes) + [START_TIME_SENTINEL] * (
        MAX_START_TIMES - len(start_minutes)
    )
    out = bytearray()
    for v in values:
        out.extend(v.to_bytes(2, "big"))
    return bytes(out)


def _stations_payload(station_to_seconds: dict[int, int]) -> bytes:
    out = bytearray(STATIONS_ROW_LEN)  # 15 bytes = 5 stations x 3 bytes
    for station_idx, seconds in station_to_seconds.items():
        offset = (station_idx - 1) * 3
        out[offset] = 0
        out[offset + 1 : offset + 3] = seconds.to_bytes(2, "big")
    return bytes(out)


def _slot_frames(
    slot_byte: int,
    *,
    name: str,
    flags: bytes,
    start_times: bytes,
    stations: bytes,
    reserved1: bytes | None = None,
    reserved5: bytes | None = None,
    tail: bytes | None = None,
) -> list[bytes]:
    """Build the 7 frames for a single slot."""
    return [
        _frame(0x3A, NAME_ROW_LEN, 0x00, slot_byte, _name_payload(name)),
        _frame(0x3A, RESERVED_ROW1_LEN, 0x01, slot_byte, reserved1 or b""),
        _frame(0x3A, FLAGS_ROW_LEN, 0x02, slot_byte, flags),
        _frame(0x3A, START_TIMES_ROW_LEN, 0x03, slot_byte, start_times),
        _frame(0x3A, STATIONS_ROW_LEN, 0x04, slot_byte, stations),
        _frame(0x3A, RESERVED_ROW5_LEN, 0x05, slot_byte, reserved5 or b""),
        _frame(0x3A, TAIL_ROW_LEN, 0x06, slot_byte, tail or b""),
    ]


# ---------------------------------------------------------------------- #
# Run 5 (final state) -- Daily + one start time each
# ---------------------------------------------------------------------- #


def test_parses_run5_final_daily_state() -> None:
    """All three programs Daily; A=07:20, B=07:50, C=no starts."""
    frames: list[bytes] = []
    # Program A: Daily, start 07:20 (440), Station 1 -> 27min (1620s).
    frames += _slot_frames(
        0x10,
        name="Program A",
        flags=_flags_payload(budget=100, freq=0x00, dow=0x7F),
        start_times=_start_times_payload([440]),
        stations=_stations_payload({1: 1620}),
    )
    # Program B: Daily, start 07:50 (470), Station 2 -> 23min (1380s).
    frames += _slot_frames(
        0x11,
        name="Program B",
        flags=_flags_payload(budget=100, freq=0x00, dow=0x7F),
        start_times=_start_times_payload([470]),
        stations=_stations_payload({2: 1380}),
    )
    # Program C: Daily, no start times, no stations.
    frames += _slot_frames(
        0x12,
        name="Program C",
        flags=_flags_payload(budget=100, freq=0x00, dow=0x7F),
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    )

    programs = parse_program_dump(frames)

    assert len(programs) == 3
    a, b, c = programs

    assert a.name == "Program A"
    assert a.frequency_type is FrequencyType.DAILY
    assert a.frequency_label == "Daily"
    assert a.water_budget == 100
    assert a.start_times == [time(7, 20)]
    assert a.stations == {1: 1620}
    assert a.has_assignments() is True

    assert b.name == "Program B"
    assert b.start_times == [time(7, 50)]
    assert b.stations == {2: 1380}

    assert c.name == "Program C"
    assert c.start_times == []
    assert c.stations == {}
    assert c.has_assignments() is False
    assert c.frequency_type is FrequencyType.DAILY


# ---------------------------------------------------------------------- #
# Run 5 frequency-type sweep -- every code we have evidence for
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "freq_byte,dow_bitmap,period,expected_type,expected_label",
    [
        (0x00, 0x7F, 0, FrequencyType.DAILY, "Daily"),
        (0x01, 0x7F, 0, FrequencyType.EVEN_DAYS, "Even days"),
        (0x02, 0x7F, 0, FrequencyType.ODD_DAYS, "Odd days"),
        (0x03, 0x7F, 0, FrequencyType.ODD_DAYS_EXCL_31, "Odd days (exclude 31st)"),
        (0x04, 0x7F, 2, FrequencyType.INTERVAL, "Every 2 days"),
        (0x04, 0x7F, 3, FrequencyType.INTERVAL, "Every 3 days"),
        (0x04, 0x7F, 7, FrequencyType.INTERVAL, "Weekly"),
        (0x04, 0x7F, 30, FrequencyType.INTERVAL, "Every 30 days"),
    ],
)
def test_frequency_type_decoding(
    freq_byte, dow_bitmap, period, expected_type, expected_label
):
    frames = _slot_frames(
        0x10,
        name="X",
        flags=_flags_payload(freq=freq_byte, dow=dow_bitmap, period=period),
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    )
    programs = parse_program_dump(frames)
    assert programs[0].frequency_type is expected_type
    assert programs[0].frequency_label == expected_label


def test_custom_frequency_decodes_dow_bitmap() -> None:
    """Run 5 captured 0x6b = Sun + Mon + Tue + Thu + Sat (Monday-first bit 0)."""
    frames = _slot_frames(
        0x10,
        name="X",
        flags=_flags_payload(freq=0x00, dow=0x6B),
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    )
    program = parse_program_dump(frames)[0]
    assert program.frequency_type is FrequencyType.CUSTOM
    assert "Mon" in program.frequency_label
    assert "Tue" in program.frequency_label
    assert "Wed" not in program.frequency_label
    assert "Thu" in program.frequency_label
    assert "Fri" not in program.frequency_label
    assert "Sat" in program.frequency_label
    assert "Sun" in program.frequency_label


# ---------------------------------------------------------------------- #
# Multiple start times (run 2 evidence)
# ---------------------------------------------------------------------- #


def test_parses_multiple_start_times_in_order() -> None:
    frames = _slot_frames(
        0x10,
        name="Program A",
        flags=_flags_payload(),
        start_times=_start_times_payload([360, 440]),  # 06:00, 07:20
        stations=_stations_payload({}),
    )
    program = parse_program_dump(frames)[0]
    assert program.start_times == [time(6, 0), time(7, 20)]


# ---------------------------------------------------------------------- #
# Multiple stations (run 3 evidence)
# ---------------------------------------------------------------------- #


def test_parses_station_assignments_independently_per_program() -> None:
    """Run 3: A=station 2 (4620s), C=station 1 (1620s), B=nothing."""
    frames = _slot_frames(
        0x10,
        name="Program A",
        flags=_flags_payload(),
        start_times=_start_times_payload([]),
        stations=_stations_payload({2: 4620}),
    ) + _slot_frames(
        0x11,
        name="Program B",
        flags=_flags_payload(),
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    ) + _slot_frames(
        0x12,
        name="Program C",
        flags=_flags_payload(),
        start_times=_start_times_payload([]),
        stations=_stations_payload({1: 1620}),
    )

    a, b, c = parse_program_dump(frames)
    assert a.stations == {2: 4620}
    assert b.stations == {}
    assert c.stations == {1: 1620}


# ---------------------------------------------------------------------- #
# Date decoding
# ---------------------------------------------------------------------- #


def test_parses_last_modified_date() -> None:
    frames = _slot_frames(
        0x10,
        name="Program A",
        flags=_flags_payload(day=26, month=5, year=2026),
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    )
    program = parse_program_dump(frames)[0]
    assert program.last_modified == date(2026, 5, 26)


def test_invalid_date_decodes_to_none_without_crashing() -> None:
    frames = _slot_frames(
        0x10,
        name="Program A",
        flags=_flags_payload(day=31, month=2, year=2026),  # 31 Feb
        start_times=_start_times_payload([]),
        stations=_stations_payload({}),
    )
    program = parse_program_dump(frames)[0]
    assert program.last_modified is None


# ---------------------------------------------------------------------- #
# Round-trip
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,freq,dow,period,days_to_next,starts,stations",
    [
        # Run 5 final: Daily, A
        ("Program A", 0x00, 0x7F, 0, 0, [440], {1: 1620}),
        # Run 5 final: Daily, B
        ("Program B", 0x00, 0x7F, 0, 0, [470], {2: 1380}),
        # Run 5 final: Daily, C (no starts, no stations)
        ("Program C", 0x00, 0x7F, 0, 0, [], {}),
        # Run 4: Every 30 days starting in 3 days
        ("Custom B", 0x04, 0x7F, 30, 3, [], {}),
        # Run 5: Custom Sun.Mo.Tu.Th.Sa
        ("Programma", 0x00, 0x6B, 0, 0, [360, 440], {}),
        # Run 3: station 2 with 4620s on Program A
        ("Program A", 0x00, 0x7F, 0, 0, [360, 440], {2: 4620}),
        # Multiple start times (8 max)
        ("Watery", 0x01, 0x7F, 0, 1, [0, 60, 120, 360, 480, 600, 720, 1380], {1: 30}),
    ],
)
def test_encode_program_round_trips_byte_perfect(
    name, freq, dow, period, days_to_next, starts, stations
) -> None:
    """encode(parse(dump)) must equal the original payloads byte-for-byte.

    Critical safety guarantee for the future write path: a read-modify-write
    cycle with no modifications must not change a single byte on the wire.
    """
    raw_rows = (
        _name_payload(name),
        b"\xa5" * RESERVED_ROW1_LEN,  # non-zero filler to catch reserved-byte loss
        _flags_payload(
            freq=freq, dow=dow, period=period, days_to_next=days_to_next
        ),
        _start_times_payload(starts),
        _stations_payload(stations),
        b"\x5a" * RESERVED_ROW5_LEN,
        b"\xff" * TAIL_ROW_LEN,
    )
    program = parse_program_slot(0, raw_rows)
    encoded = encode_program(program)
    assert encoded == raw_rows, (
        f"Round-trip mismatch:\n  expected={raw_rows!r}\n  got     ={encoded!r}"
    )


def test_encode_program_preserves_reserved_bytes_after_partial_modification() -> None:
    """Modifying a known field must not touch any reserved byte."""
    reserved_row1 = b"\xa5" * RESERVED_ROW1_LEN
    reserved_row5 = b"\x5a" * RESERVED_ROW5_LEN
    reserved_tail = b"\xff" * TAIL_ROW_LEN
    reserved_flags_leading = bytes([0x77, 0x88, 0x99])  # bytes 0..2 of flags row

    raw_rows = (
        _name_payload("Program A"),
        reserved_row1,
        reserved_flags_leading + _flags_payload()[3:],
        _start_times_payload([440]),
        _stations_payload({1: 600}),
        reserved_row5,
        reserved_tail,
    )
    original = parse_program_slot(0, raw_rows)
    modified = apply_program_changes(original, name="Mattina", water_budget=80)
    encoded = encode_program(modified)

    # Reserved rows verbatim
    assert encoded[1] == reserved_row1
    assert encoded[5] == reserved_row5
    assert encoded[6] == reserved_tail
    # Reserved leading 3 bytes of the flags row preserved
    assert encoded[2][:3] == reserved_flags_leading
    # Name actually changed
    assert encoded[0] == _name_payload("Mattina")
    # Water budget byte updated
    assert encoded[2][3] == 80


def test_apply_program_changes_only_overrides_provided_fields() -> None:
    program = Program(
        slot=0,
        name="Old",
        water_budget=100,
        frequency_type=FrequencyType.DAILY,
        frequency_label="Daily",
        dow_bitmap=0x7F,
        period_days=0,
        days_to_next=0,
        last_modified=date(2026, 5, 26),
    )
    updated = apply_program_changes(program, name="New", water_budget=None)
    assert updated.name == "New"
    assert updated.water_budget == 100  # None was filtered out


# ---------------------------------------------------------------------- #
# Robustness
# ---------------------------------------------------------------------- #


def test_parse_program_dump_skips_partial_slots() -> None:
    """Slots with fewer than 7 rows must be silently skipped, not crash."""
    only_name = [
        _frame(0x3A, NAME_ROW_LEN, 0x00, 0x10, _name_payload("Partial")),
    ]
    assert parse_program_dump(only_name) == []


def test_parse_program_dump_skips_empty_input() -> None:
    assert parse_program_dump([]) == []


def test_program_slot_count_matches_firmware_layout() -> None:
    """Sanity: the firmware reserves exactly 12 slots."""
    assert PROGRAM_SLOT_COUNT == 12


# ---------------------------------------------------------------------- #
# Write-side helpers
# ---------------------------------------------------------------------- #


def test_build_program_write_frames_round_trips_through_read_parser() -> None:
    """Frames built for writing must parse back to an identical program.

    Critical sanity check: the write encoder and read parser must agree
    on the byte layout. This prevents a "writes-and-reads-look-fine-in-
    isolation-but-disagree-with-each-other" trap.
    """
    from custom_components.another_solem_bluetooth_watering_controller.programs import (
        build_program_write_frames,
    )

    raw_rows = (
        _name_payload("Program A"),
        b"\x00" * RESERVED_ROW1_LEN,
        _flags_payload(),
        _start_times_payload([440]),
        _stations_payload({1: 600}),
        b"\x00" * RESERVED_ROW5_LEN,
        b"\x00" * TAIL_ROW_LEN,
    )
    write_frames = build_program_write_frames(slot=0, rows=raw_rows)
    assert len(write_frames) == 7
    # Replay through the read parser by rewriting them with the 0x3a read
    # opcode: the parser uses the opcode only to verify frame validity in
    # the client layer; here we just feed the dump shape it expects.
    read_frames = [bytes([0x3A]) + write_frame[1:] for write_frame in write_frames]

    programs = parse_program_dump(read_frames)
    assert programs[0].name == "Program A"
    assert programs[0].start_times == [time(7, 20)]
    assert programs[0].stations == {1: 600}


def test_unknown_diff_locations_detects_reserved_byte_changes() -> None:
    """Touching a reserved byte must be flagged by the safety net."""
    from custom_components.another_solem_bluetooth_watering_controller.programs import (
        unknown_diff_locations,
    )

    original = (
        _name_payload("Program A"),
        b"\xa5" * RESERVED_ROW1_LEN,
        _flags_payload(),
        _start_times_payload([]),
        _stations_payload({}),
        b"\x00" * RESERVED_ROW5_LEN,
        b"\x00" * TAIL_ROW_LEN,
    )
    modified = list(original)
    # Tamper with reserved row 1 -- this is NOT in KNOWN_WRITABLE_OFFSETS.
    modified[1] = b"\x00" * RESERVED_ROW1_LEN
    bad = unknown_diff_locations(original, tuple(modified))
    assert bad, "Reserved row 1 byte change must be flagged"
    assert all(row_idx == 1 for row_idx, _ in bad)


def test_unknown_diff_locations_allows_known_offsets() -> None:
    """Changes within decoded offsets should pass the safety net."""
    from custom_components.another_solem_bluetooth_watering_controller.programs import (
        unknown_diff_locations,
    )

    original = (
        _name_payload("Program A"),
        b"\x00" * RESERVED_ROW1_LEN,
        _flags_payload(budget=100),
        _start_times_payload([]),
        _stations_payload({}),
        b"\x00" * RESERVED_ROW5_LEN,
        b"\x00" * TAIL_ROW_LEN,
    )
    modified = (
        _name_payload("Mattina"),  # change row 0
        original[1],
        _flags_payload(budget=80),  # change byte 3 of flags row
        original[3],
        original[4],
        original[5],
        original[6],
    )
    assert unknown_diff_locations(original, modified) == []
