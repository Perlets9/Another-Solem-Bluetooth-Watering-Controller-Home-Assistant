"""Tests for the integration's Home Assistant services.

We exercise the pure-Python translation helpers (no HA harness needed):
service-call payloads -> ``apply_program_changes`` kwargs and the schema
validation that catches malformed input before it ever hits the BLE layer.
"""

from __future__ import annotations

from datetime import time

import pytest
import voluptuous as vol

from custom_components.another_solem_bluetooth_watering_controller.programs import (
    FrequencyType,
)
from custom_components.another_solem_bluetooth_watering_controller.services import (
    _CONFIGURE_PROGRAM_SCHEMA,
    _build_program_changes,
)


# ---------------------------------------------------------------------- #
# _build_program_changes
# ---------------------------------------------------------------------- #


def test_build_changes_empty_call_yields_no_changes() -> None:
    # Only ``program`` and ``device_id`` are present: nothing to update.
    assert _build_program_changes({"program": 1, "device_id": ["abc"]}) == {}


def test_build_changes_translates_every_field() -> None:
    changes = _build_program_changes(
        {
            "program": 1,
            "device_id": ["abc"],
            "name": "Morning",
            "water_budget": 80,
            "frequency": "custom",
            "period_days": 3,
            "days_of_week": ["mon", "wed", "fri"],
            "start_times": [time(7, 20), time(19, 0)],
            "stations": {1: 27, 2: 15},
        }
    )
    assert changes == {
        "name": "Morning",
        "water_budget": 80,
        "frequency_type": FrequencyType.CUSTOM,
        "period_days": 3,
        # Mon=bit0, Wed=bit2, Fri=bit4 -> 0b0010101 = 0x15.
        "dow_bitmap": 0x15,
        "start_times": [time(7, 20), time(19, 0)],
        # Service payload is in minutes; firmware stores seconds.
        "stations": {1: 27 * 60, 2: 15 * 60},
    }


def test_build_changes_days_of_week_full_week_yields_all_bits() -> None:
    changes = _build_program_changes(
        {
            "program": 1,
            "device_id": ["abc"],
            "days_of_week": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        }
    )
    assert changes == {"dow_bitmap": 0x7F}


def test_build_changes_stations_empty_dict_deassigns_all() -> None:
    changes = _build_program_changes(
        {
            "program": 1,
            "device_id": ["abc"],
            "stations": {},
        }
    )
    # An explicit empty mapping must still be forwarded so the encoder
    # writes zeros for every station entry (full deassignment).
    assert changes == {"stations": {}}


def test_build_changes_start_times_empty_list_clears_schedule() -> None:
    changes = _build_program_changes(
        {
            "program": 1,
            "device_id": ["abc"],
            "start_times": [],
        }
    )
    assert changes == {"start_times": []}


# ---------------------------------------------------------------------- #
# Schema validation
# ---------------------------------------------------------------------- #


def _valid_payload(**extras) -> dict:
    return {"device_id": ["abc"], "program": 1, **extras}


def test_schema_accepts_minimal_payload() -> None:
    out = _CONFIGURE_PROGRAM_SCHEMA(_valid_payload())
    assert out["program"] == 1


def test_schema_parses_start_times_strings_into_time_objects() -> None:
    out = _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(start_times=["07:20", "19:00"]))
    assert out["start_times"] == [time(7, 20), time(19, 0)]


def test_schema_rejects_too_many_start_times() -> None:
    # Firmware caps the start-times row at 8 entries.
    payload = _valid_payload(start_times=[f"0{i}:00" for i in range(9)])
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(payload)


def test_schema_rejects_malformed_time_string() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(start_times=["7h20"]))


def test_schema_rejects_out_of_range_clock() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(start_times=["25:00"]))


def test_schema_rejects_unknown_frequency() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(frequency="hourly"))


def test_schema_rejects_water_budget_above_200() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(water_budget=250))


def test_schema_rejects_station_index_zero() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(stations={0: 10}))


def test_schema_allows_station_duration_zero_for_deassign() -> None:
    out = _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(stations={1: 0}))
    assert out["stations"] == {1: 0}


def test_schema_rejects_unknown_day_of_week() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(days_of_week=["funday"]))


def test_schema_rejects_program_outside_1_3() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA({"device_id": ["abc"], "program": 4})
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA({"device_id": ["abc"], "program": 0})


def test_schema_rejects_name_longer_than_16() -> None:
    with pytest.raises(vol.Invalid):
        _CONFIGURE_PROGRAM_SCHEMA(_valid_payload(name="a" * 17))
