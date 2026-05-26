"""Tests for SOLEM BL-IP switch state handling."""

from types import SimpleNamespace

from custom_components.another_solem_bluetooth_watering_controller.const import CONF_NAME
from custom_components.another_solem_bluetooth_watering_controller.protocol import (
    SolemMode,
    SolemStatus,
)
from custom_components.another_solem_bluetooth_watering_controller.switch import (
    AllStationsSwitch,
    RunProgramSwitch,
    StationSwitch,
)


def _coordinator(active_station: int | None, *, active_program: int | None = None):
    return SimpleNamespace(
        address="AA:BB:CC",
        data=SolemStatus(SolemMode.SINGLE_STATION_ACTIVE, True, 300, "raw"),
        active_station=active_station,
        active_program=active_program,
        device_info=None,
        programs=None,
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


def test_switches_are_unknown_when_status_has_not_been_read() -> None:
    coordinator = _coordinator(active_station=None)
    coordinator.data = None

    assert StationSwitch(coordinator, 1).is_on is None
    assert AllStationsSwitch(coordinator).is_on is None


def test_run_program_switch_is_on_only_for_matching_active_program() -> None:
    coordinator = _coordinator(active_station=None, active_program=2)

    assert RunProgramSwitch(coordinator, 1).is_on is False
    assert RunProgramSwitch(coordinator, 2).is_on is True
    assert RunProgramSwitch(coordinator, 3).is_on is False


def test_run_program_switch_is_off_when_controller_is_idle() -> None:
    coordinator = _coordinator(active_station=None, active_program=2)
    coordinator.data = SolemStatus(SolemMode.IDLE, False, 0, "raw")

    assert RunProgramSwitch(coordinator, 2).is_on is False


def test_run_program_switch_default_name_used_when_programs_unknown() -> None:
    coordinator = _coordinator(active_station=None, active_program=None)

    switch = RunProgramSwitch(coordinator, 1)
    assert switch.name == "Program A"


def test_run_program_switch_uses_program_name_when_available() -> None:
    from custom_components.another_solem_bluetooth_watering_controller.programs import (
        FrequencyType,
        Program,
    )

    coordinator = _coordinator(active_station=None, active_program=None)
    coordinator.programs = [
        Program(
            slot=0,
            name="Mattina",
            water_budget=100,
            frequency_type=FrequencyType.DAILY,
            frequency_label="Daily",
            dow_bitmap=0x7F,
            period_days=0,
            days_to_next=0,
            last_modified=None,
        )
    ]

    switch = RunProgramSwitch(coordinator, 1)
    assert switch.name == "Mattina"
