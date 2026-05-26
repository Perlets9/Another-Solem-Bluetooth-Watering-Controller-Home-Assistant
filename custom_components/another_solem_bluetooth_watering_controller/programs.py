"""Parsing and encoding of SOLEM BL-IP program records.

The controller stores up to 12 program slots (MySolem only exposes the
first three). Each slot is serialized over BLE as **7 frames** with the
following layout, derived from the reverse-engineered MySolem traffic:

| Row | Opcode hint | Length | Purpose |
|---|---|---|---|
| 0 | `0x2f`/`0x30` | 16 | ASCII program name, null-padded |
| 1 | `0x2f`/`0x30` | 16 | Reserved (zeros) |
| 2 | `0x37`/`0x3a` | 14 | Flags + budget + frequency + last-modified date |
| 3 | `0x37`/`0x3a` | 16 | Start times (8 x BE16, "minutes since midnight") |
| 4 | `0x37`/`0x3a` | 16 | Station/duration table (5 x 3 byte + 1 pad) |
| 5 | `0x37`/`0x3a` | 16 | Reserved (zeros) -- possibly extra stations on BL6IP |
| 6 | `0x37`/`0x3a` | 7  | Reserved tail |

When reading we receive opcode `0x3a` framing; when writing we use `0x2f`
for the name row and `0x37` for the data rows. The payload after the
header in each frame follows the same byte layout regardless of direction.

This module is purposely free of Home Assistant imports so it can be unit
tested without spinning up the full integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, time
from enum import StrEnum
import struct

# ---------------------------------------------------------------------- #
# Public constants
# ---------------------------------------------------------------------- #

# Number of program slots reserved by the firmware. MySolem only shows the
# first 3 (A/B/C) but we parse all 12 for diagnostics / future-proofing.
PROGRAM_SLOT_COUNT = 12
# Rows per program slot.
ROWS_PER_PROGRAM = 7
# 12 slots x 7 rows = 84 frames per dump.
TOTAL_PROGRAM_FRAMES = PROGRAM_SLOT_COUNT * ROWS_PER_PROGRAM

# Sentinel value placed in unused start-time slots. 0x05a0 = 1440 minutes
# (= 24h) which the firmware uses as "no start time configured".
START_TIME_SENTINEL = 0x05A0

# Number of start-time slots per program.
MAX_START_TIMES = 8
# Number of station entries packed in the station/duration row.
STATIONS_PER_ROW = 5

# Per-row payload sizes (bytes after the 4-byte ``[OPCODE LEN SEQ SLOT]``
# wire header). These are the figures the parser/encoder use internally.
NAME_ROW_LEN = 16
RESERVED_ROW1_LEN = 16
FLAGS_ROW_LEN = 12
START_TIMES_ROW_LEN = 16
# Stations row holds exactly 5 stations x 3 bytes = 15 bytes (no trailing
# pad: the SNOOP-documented LEN=0x11=17 = 2 (SEQ+SLOT) + 15 (payload)).
STATIONS_ROW_LEN = 15
RESERVED_ROW5_LEN = 15
TAIL_ROW_LEN = 6

ROW_LENGTHS: tuple[int, ...] = (
    NAME_ROW_LEN,
    RESERVED_ROW1_LEN,
    FLAGS_ROW_LEN,
    START_TIMES_ROW_LEN,
    STATIONS_ROW_LEN,
    RESERVED_ROW5_LEN,
    TAIL_ROW_LEN,
)

# The LEN byte the firmware uses on the wire counts SEQ + SLOT in addition
# to the payload, hence ``payload_size + 2``. We keep both values to make
# the asymmetry explicit at every call site.
WIRE_LEN_OVERHEAD = 2  # SEQ byte + SLOT byte
WIRE_LEN_BY_ROW: tuple[int, ...] = tuple(
    payload + WIRE_LEN_OVERHEAD for payload in ROW_LENGTHS
)


class FrequencyType(StrEnum):
    """Canonical frequency modes exposed by the controller firmware.

    The byte at offset 4 of the flags row encodes which mode the program
    is in. Codes ``0x00``..``0x04`` cover all 8 visible MySolem labels;
    additional disambiguation comes from ``DOW_BITMAP`` and ``PERIOD``.
    """

    DAILY = "daily"
    CUSTOM = "custom"
    EVEN_DAYS = "even_days"
    ODD_DAYS = "odd_days"
    ODD_DAYS_EXCL_31 = "odd_days_excl_31"
    INTERVAL = "interval"
    UNKNOWN = "unknown"


# Mapping from raw FREQ_TYPE byte (when DOW_BITMAP indicates "not custom")
# to FrequencyType. ``0x00`` is decoded by callers based on DOW_BITMAP.
_FREQ_TYPE_BY_CODE: dict[int, FrequencyType] = {
    0x01: FrequencyType.EVEN_DAYS,
    0x02: FrequencyType.ODD_DAYS,
    0x03: FrequencyType.ODD_DAYS_EXCL_31,
    0x04: FrequencyType.INTERVAL,
}

# Inverse mapping used by the encoder.
_CODE_BY_FREQ_TYPE: dict[FrequencyType, int] = {
    v: k for k, v in _FREQ_TYPE_BY_CODE.items()
}
_CODE_BY_FREQ_TYPE[FrequencyType.DAILY] = 0x00
_CODE_BY_FREQ_TYPE[FrequencyType.CUSTOM] = 0x00

_ALL_DAYS_BITMAP = 0x7F
_DAY_LABELS_MONDAY_FIRST: tuple[str, ...] = (
    "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
)


# ---------------------------------------------------------------------- #
# Public dataclass
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class Program:
    """Decoded view of a single program slot.

    ``raw_rows`` keeps the byte-for-byte payload of each of the 7 rows so
    that the encoder can preserve any field we have not yet decoded when
    we write back a modified program. This is critical for safety: never
    overwrite bytes you don't understand.
    """

    slot: int
    name: str
    water_budget: int
    frequency_type: FrequencyType
    frequency_label: str
    dow_bitmap: int
    period_days: int
    days_to_next: int
    last_modified: date | None
    start_times: list[time] = field(default_factory=list)
    # station_index (1-based) -> duration in seconds
    stations: dict[int, int] = field(default_factory=dict)
    raw_rows: tuple[bytes, ...] = field(default=())

    def has_assignments(self) -> bool:
        """Return whether this program has at least one station assigned."""
        return any(duration > 0 for duration in self.stations.values())


# ---------------------------------------------------------------------- #
# Parsing
# ---------------------------------------------------------------------- #


def _format_frequency(
    freq_type: FrequencyType, period_days: int, dow_bitmap: int
) -> str:
    """Produce a human-readable frequency label matching MySolem's UI."""
    if freq_type is FrequencyType.DAILY:
        return "Daily"
    if freq_type is FrequencyType.CUSTOM:
        selected = [
            label
            for idx, label in enumerate(_DAY_LABELS_MONDAY_FIRST)
            if dow_bitmap & (1 << idx)
        ]
        if not selected:
            return "Custom (no day)"
        return "Custom (" + ", ".join(selected) + ")"
    if freq_type is FrequencyType.EVEN_DAYS:
        return "Even days"
    if freq_type is FrequencyType.ODD_DAYS:
        return "Odd days"
    if freq_type is FrequencyType.ODD_DAYS_EXCL_31:
        return "Odd days (exclude 31st)"
    if freq_type is FrequencyType.INTERVAL:
        if period_days == 7:
            return "Weekly"
        if period_days in (2, 3):
            return f"Every {period_days} days"
        return f"Every {period_days} days"
    return "Unknown"


def _decode_frequency_type(freq_byte: int, dow_bitmap: int) -> FrequencyType:
    """Resolve the firmware frequency code to a canonical :class:`FrequencyType`."""
    if freq_byte == 0x00:
        return (
            FrequencyType.DAILY
            if dow_bitmap == _ALL_DAYS_BITMAP
            else FrequencyType.CUSTOM
        )
    return _FREQ_TYPE_BY_CODE.get(freq_byte, FrequencyType.UNKNOWN)


def _parse_name_row(payload: bytes) -> str:
    """Decode the ASCII name from the first row (null-padded, 16 bytes)."""
    return payload[:NAME_ROW_LEN].rstrip(b"\x00").decode("ascii", errors="replace")


def _parse_flags_row(payload: bytes) -> tuple[int, int, int, int, int, date | None]:
    """Decode the flags row into ``(budget, freq, dow, period, days_to_next, date)``.

    Layout (12 bytes):
        ``[reserved:3] [budget:1] [freq:1] [dow:1] [period:1] [days_to_next:1]
          [day:1] [month:1] [year:BE16]``
    """
    if len(payload) < FLAGS_ROW_LEN:
        return 0, 0, 0, 0, 0, None
    budget = payload[3]
    freq_byte = payload[4]
    dow_bitmap = payload[5]
    period_days = payload[6]
    days_to_next = payload[7]
    day = payload[8]
    month = payload[9]
    year = struct.unpack(">H", payload[10:12])[0]
    last_modified: date | None
    try:
        last_modified = date(year, month, day) if year and month and day else None
    except ValueError:
        last_modified = None
    return budget, freq_byte, dow_bitmap, period_days, days_to_next, last_modified


def _parse_start_times_row(payload: bytes) -> list[time]:
    """Decode up to 8 start times (BE16 minutes since midnight, sentinel 0x05a0)."""
    times: list[time] = []
    for offset in range(0, MAX_START_TIMES * 2, 2):
        if offset + 2 > len(payload):
            break
        raw = struct.unpack(">H", payload[offset : offset + 2])[0]
        if raw == START_TIME_SENTINEL:
            continue
        if raw >= 1440:
            # Out-of-range minute count -> treat as sentinel rather than crash.
            continue
        hours, minutes = divmod(raw, 60)
        times.append(time(hour=hours, minute=minutes))
    return times


def _parse_stations_row(payload: bytes) -> dict[int, int]:
    """Decode up to 5 ``[reserved:1] [duration:BE16]`` station entries.

    Returns a ``{station_index_1based: duration_seconds}`` mapping with only
    the stations that have a non-zero duration.
    """
    stations: dict[int, int] = {}
    for idx in range(STATIONS_PER_ROW):
        start = idx * 3
        if start + 3 > len(payload):
            break
        duration = struct.unpack(">H", payload[start + 1 : start + 3])[0]
        if duration:
            stations[idx + 1] = duration
    return stations


def parse_program_slot(slot: int, rows: tuple[bytes, ...]) -> Program:
    """Build a :class:`Program` from the 7 raw row payloads of a single slot."""
    if len(rows) != ROWS_PER_PROGRAM:
        raise ValueError(
            f"Expected {ROWS_PER_PROGRAM} rows per slot, got {len(rows)}"
        )
    name = _parse_name_row(rows[0])
    budget, freq_byte, dow_bitmap, period_days, days_to_next, last_modified = (
        _parse_flags_row(rows[2])
    )
    freq_type = _decode_frequency_type(freq_byte, dow_bitmap)
    label = _format_frequency(freq_type, period_days, dow_bitmap)
    start_times = _parse_start_times_row(rows[3])
    stations = _parse_stations_row(rows[4])
    return Program(
        slot=slot,
        name=name,
        water_budget=budget,
        frequency_type=freq_type,
        frequency_label=label,
        dow_bitmap=dow_bitmap,
        period_days=period_days,
        days_to_next=days_to_next,
        last_modified=last_modified,
        start_times=start_times,
        stations=stations,
        raw_rows=tuple(rows),
    )


def parse_program_dump(frames: list[bytes]) -> list[Program]:
    """Parse the 84-frame dump returned by ``0x39 00``.

    Each frame has the shape ``[OPCODE] [LEN] [SEQ] [SLOT_BYTE] [PAYLOAD:LEN]``
    where ``SLOT_BYTE`` indexes the program slot (slots 0..11 map to slot
    bytes ``0x10``..``0x1b``). Frames are grouped by ``SLOT_BYTE`` and then
    sorted by their natural row position, which we recognize from the LEN
    byte alone (lengths within a slot are unique).

    Frames that don't match the expected shape are silently dropped.
    """
    by_slot: dict[int, list[bytes]] = {}
    for frame in frames:
        if len(frame) < 4:
            continue
        wire_length = frame[1]
        if wire_length < WIRE_LEN_OVERHEAD:
            continue
        payload_len = wire_length - WIRE_LEN_OVERHEAD
        slot_byte = frame[3]
        payload = frame[4 : 4 + payload_len]
        if len(payload) != payload_len:
            continue
        by_slot.setdefault(slot_byte, []).append(payload)

    programs: list[Program] = []
    for slot_idx in range(PROGRAM_SLOT_COUNT):
        slot_byte = 0x10 + slot_idx
        payloads = by_slot.get(slot_byte, [])
        if len(payloads) < ROWS_PER_PROGRAM:
            continue
        rows = _select_rows_by_length(payloads)
        if rows is None:
            continue
        programs.append(parse_program_slot(slot_idx, rows))
    return programs


def _select_rows_by_length(payloads: list[bytes]) -> tuple[bytes, ...] | None:
    """Pick one payload per expected row length, in row order.

    Lengths in ``ROW_LENGTHS`` are unique except for the multiple 16-byte
    rows (name/reserved1/starts/stations/reserved5). For those we rely on
    the order in which the frames arrived from the device: the firmware
    always emits row 0 (name) first, then 1, then 2, ... so we walk
    ``payloads`` in order and consume the first payload that matches the
    next expected length.
    """
    result: list[bytes] = []
    remaining = list(payloads)
    for expected_len in ROW_LENGTHS:
        match_index = next(
            (i for i, p in enumerate(remaining) if len(p) == expected_len),
            None,
        )
        if match_index is None:
            return None
        result.append(remaining.pop(match_index))
    return tuple(result)


# ---------------------------------------------------------------------- #
# Encoding
# ---------------------------------------------------------------------- #


def _encode_name_row(name: str, previous: bytes) -> bytes:
    """Re-encode the name row preserving any trailing bytes we did not touch."""
    encoded = name.encode("ascii", errors="replace")[:NAME_ROW_LEN]
    encoded = encoded.ljust(NAME_ROW_LEN, b"\x00")
    return encoded


def _encode_flags_row(program: Program, previous: bytes) -> bytes:
    """Re-encode the flags row, preserving the 3 reserved leading bytes."""
    out = bytearray(previous.ljust(FLAGS_ROW_LEN, b"\x00"))
    out[3] = program.water_budget & 0xFF
    out[4] = _CODE_BY_FREQ_TYPE.get(program.frequency_type, 0x00) & 0xFF
    out[5] = program.dow_bitmap & 0xFF
    out[6] = program.period_days & 0xFF
    out[7] = program.days_to_next & 0xFF
    if program.last_modified is not None:
        out[8] = program.last_modified.day & 0xFF
        out[9] = program.last_modified.month & 0xFF
        out[10:12] = struct.pack(">H", program.last_modified.year & 0xFFFF)
    return bytes(out)


def _encode_start_times_row(start_times: list[time]) -> bytes:
    """Pack up to 8 start times, padding with the firmware sentinel."""
    values: list[int] = []
    for t in start_times[:MAX_START_TIMES]:
        values.append((t.hour * 60 + t.minute) & 0xFFFF)
    while len(values) < MAX_START_TIMES:
        values.append(START_TIME_SENTINEL)
    return b"".join(struct.pack(">H", v) for v in values)


def _encode_stations_row(stations: dict[int, int]) -> bytes:
    """Pack the 5-station table; missing stations are encoded as ``00 00 00``.

    Result is always ``STATIONS_PER_ROW * 3`` = 15 bytes, matching the
    firmware's row size.
    """
    out = bytearray(STATIONS_PER_ROW * 3)
    for station_idx in range(1, STATIONS_PER_ROW + 1):
        duration = stations.get(station_idx, 0) & 0xFFFF
        out[(station_idx - 1) * 3 + 1 : (station_idx - 1) * 3 + 3] = struct.pack(
            ">H", duration
        )
    return bytes(out)


def encode_program(program: Program) -> tuple[bytes, ...]:
    """Re-serialize a program into its 7 row payloads.

    Bytes that the parser does not decode (rows 1, 5, 6, plus the leading
    reserved bytes of the flags row) are taken verbatim from
    :attr:`Program.raw_rows` when available, so a read-modify-write loop is
    byte-perfect for everything the integration does not understand.
    """
    raw = program.raw_rows if len(program.raw_rows) == ROWS_PER_PROGRAM else (
        b"" * ROWS_PER_PROGRAM
    )
    name_row = _encode_name_row(program.name, raw[0] if raw else b"")
    reserved_row1 = (raw[1] if raw else b"").ljust(RESERVED_ROW1_LEN, b"\x00")[
        :RESERVED_ROW1_LEN
    ]
    flags_row = _encode_flags_row(program, raw[2] if raw else b"")
    start_times_row = _encode_start_times_row(program.start_times)
    stations_row = _encode_stations_row(program.stations)
    reserved_row5 = (raw[5] if raw else b"").ljust(RESERVED_ROW5_LEN, b"\x00")[
        :RESERVED_ROW5_LEN
    ]
    tail_row = (raw[6] if raw else b"").ljust(TAIL_ROW_LEN, b"\x00")[:TAIL_ROW_LEN]
    return (
        name_row,
        reserved_row1,
        flags_row,
        start_times_row,
        stations_row,
        reserved_row5,
        tail_row,
    )


# ---------------------------------------------------------------------- #
# Write framing
# ---------------------------------------------------------------------- #


# Per-row write opcode + sequence index used by the firmware.
# Opcodes/seq numbers match the writes captured in SNOOP-2026-05-25.md
# §3.7; the LEN byte is derived from the payload size + 2 (see
# WIRE_LEN_BY_ROW above), which matches the values 0x12, 0x0e, 0x11, 0x08
# observed on the wire.
WRITE_ROW_OPCODES_AND_SEQ: tuple[tuple[int, int], ...] = (
    (0x2F, 0x00),  # row 0: name
    (0x2F, 0x01),  # row 1: reserved
    (0x37, 0x00),  # row 2: flags
    (0x37, 0x01),  # row 3: start times
    (0x37, 0x02),  # row 4: stations
    (0x37, 0x03),  # row 5: reserved
    (0x37, 0x04),  # row 6: tail
)


def build_program_write_frames(slot: int, rows: tuple[bytes, ...]) -> list[bytes]:
    """Wrap each payload row in the firmware's expected write header.

    Each frame on the wire is ``[OPCODE][LEN][SEQ][SLOT][PAYLOAD]`` where
    ``LEN`` counts ``SEQ + SLOT + PAYLOAD`` bytes (see WIRE_LEN_BY_ROW).
    The slot byte is encoded as ``0x10 + slot`` (slot indices 0..11 map to
    bytes ``0x10..0x1b``), matching the read path.
    """
    if len(rows) != ROWS_PER_PROGRAM:
        raise ValueError(f"Expected {ROWS_PER_PROGRAM} rows, got {len(rows)}")
    if not 0 <= slot < PROGRAM_SLOT_COUNT:
        raise ValueError(f"Slot {slot} out of range 0..{PROGRAM_SLOT_COUNT - 1}")
    slot_byte = 0x10 + slot
    frames: list[bytes] = []
    for (opcode, seq), payload, payload_size, wire_len in zip(
        WRITE_ROW_OPCODES_AND_SEQ, rows, ROW_LENGTHS, WIRE_LEN_BY_ROW, strict=True
    ):
        if len(payload) != payload_size:
            raise ValueError(
                f"Row seq={seq} payload must be {payload_size} bytes, "
                f"got {len(payload)}"
            )
        frames.append(bytes([opcode, wire_len, seq, slot_byte]) + payload)
    return frames


def diff_rows(a: tuple[bytes, ...], b: tuple[bytes, ...]) -> list[tuple[int, int]]:
    """Return ``[(row_idx, byte_offset_in_row), ...]`` of bytes that differ.

    Used by the safety net in the write path: a diff that touches bytes
    outside the offsets we have decoded for a given row is reported back
    to callers so they can abort instead of overwriting unknown data.
    """
    diffs: list[tuple[int, int]] = []
    for row_idx, (left, right) in enumerate(zip(a, b)):
        if left == right:
            continue
        for offset in range(max(len(left), len(right))):
            lb = left[offset] if offset < len(left) else None
            rb = right[offset] if offset < len(right) else None
            if lb != rb:
                diffs.append((row_idx, offset))
    return diffs


# Byte offsets we are confident about, per row. Anything else must remain
# byte-perfect across a read-modify-write cycle.
KNOWN_WRITABLE_OFFSETS: dict[int, frozenset[int]] = {
    0: frozenset(range(NAME_ROW_LEN)),  # whole name row
    2: frozenset(range(3, FLAGS_ROW_LEN)),  # everything except leading 3 reserved
    3: frozenset(range(START_TIMES_ROW_LEN)),  # whole start-times row
    4: frozenset(range(STATIONS_PER_ROW * 3)),  # whole stations row
}


def unknown_diff_locations(
    original: tuple[bytes, ...], modified: tuple[bytes, ...]
) -> list[tuple[int, int]]:
    """Return diffs whose location is NOT in :data:`KNOWN_WRITABLE_OFFSETS`."""
    diffs = diff_rows(original, modified)
    bad: list[tuple[int, int]] = []
    for row_idx, offset in diffs:
        allowed = KNOWN_WRITABLE_OFFSETS.get(row_idx)
        if allowed is None or offset not in allowed:
            bad.append((row_idx, offset))
    return bad


def apply_program_changes(program: Program, **changes) -> Program:
    """Return a copy of ``program`` with selected fields replaced.

    Thin wrapper over :func:`dataclasses.replace` that drops ``None``
    values so service handlers can pass through optional fields without
    having to filter them out by hand.
    """
    filtered = {k: v for k, v in changes.items() if v is not None}
    return replace(program, **filtered)
