"""Tests for SOLEM BL-IP coordinator helpers."""

import asyncio
from datetime import timedelta
from types import MethodType

import pytest

from custom_components.another_solem_bluetooth_watering_controller.const import (
    CONF_ACTIVE_POLL_INTERVAL,
    CONF_IDLE_POLL_INTERVAL,
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
)
from custom_components.another_solem_bluetooth_watering_controller.coordinator import (
    SolemCoordinator,
    active_poll_interval,
    idle_poll_interval,
    polling_update_interval,
)
from custom_components.another_solem_bluetooth_watering_controller.protocol import SolemMode, SolemStatus


class FakeClient:
    """Fake SOLEM client for coordinator queue tests."""

    def __init__(self) -> None:
        self.commands: list[bytes] = []
        self.reads = 0
        self.status = SolemStatus(SolemMode.IDLE, False, 0, "read")

    async def send_command(self, command: bytes) -> None:
        self.commands.append(command)

    async def read_status(self) -> SolemStatus:
        self.reads += 1
        return self.status


def _coordinator(
    default_duration: int = 20,
    ble_device_available: bool = True,
    idle_interval: timedelta | None = timedelta(seconds=600),
    active_interval: timedelta = timedelta(seconds=30),
) -> SolemCoordinator:
    coordinator = SolemCoordinator.__new__(SolemCoordinator)
    coordinator.default_duration = default_duration
    coordinator.active_station = None
    coordinator.active_program = None
    coordinator.client = FakeClient()
    coordinator.data = SolemStatus(SolemMode.IDLE, False, 0, "initial")
    coordinator._ble_operation_lock = asyncio.Lock()
    coordinator._manual_command_pending = False
    coordinator._idle_interval = idle_interval
    coordinator._active_interval = active_interval
    coordinator.update_interval = idle_interval
    coordinator.rssi = None
    coordinator._bluetooth_state_listeners = set()
    coordinator.device_info = None
    coordinator._watering_started_at = None
    coordinator._watering_station = None
    coordinator.last_watering_time = None
    coordinator.last_watering_station = None
    coordinator.last_watering_duration = None
    coordinator.programs = None
    coordinator.programs_last_refresh = None
    coordinator._programs_refresh_interval = timedelta(seconds=21600)
    coordinator._programs_listeners = set()
    coordinator._async_set_latest_ble_device = MethodType(
        lambda self: ble_device_available,
        coordinator,
    )
    coordinator.async_set_updated_data = MethodType(
        lambda self, data: None,
        coordinator,
    )
    return coordinator


def _record_coordinator_updates(coordinator: SolemCoordinator) -> list[SolemStatus | None]:
    updates: list[SolemStatus | None] = []

    def _record_update(self, data):
        updates.append(data)
        self.data = data

    coordinator.async_set_updated_data = MethodType(_record_update, coordinator)
    return updates


# ---------------------------------------------------------------------- #
# Polling interval helpers
# ---------------------------------------------------------------------- #


def test_idle_poll_interval_uses_new_idle_setting_when_present() -> None:
    interval = idle_poll_interval({CONF_POLLING_ENABLED: True, CONF_IDLE_POLL_INTERVAL: 300})
    assert interval == timedelta(seconds=300)


def test_idle_poll_interval_falls_back_to_legacy_poll_interval() -> None:
    """Old config entries (CONF_POLL_INTERVAL only) must keep working unchanged."""
    interval = idle_poll_interval({CONF_POLLING_ENABLED: True, CONF_POLL_INTERVAL: 45})
    assert interval == timedelta(seconds=45)


def test_idle_poll_interval_is_none_when_disabled() -> None:
    assert idle_poll_interval({CONF_POLLING_ENABLED: False}) is None


def test_polling_update_interval_alias_returns_idle_interval() -> None:
    assert polling_update_interval(
        {CONF_POLLING_ENABLED: True, CONF_IDLE_POLL_INTERVAL: 600}
    ) == timedelta(seconds=600)


def test_active_poll_interval_uses_default_when_missing() -> None:
    assert active_poll_interval({}) == timedelta(seconds=30)


def test_active_poll_interval_uses_configured_value() -> None:
    assert active_poll_interval({CONF_ACTIVE_POLL_INTERVAL: 15}) == timedelta(seconds=15)


# ---------------------------------------------------------------------- #
# Commands: post-command refresh and adaptive interval
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_station_refreshes_state_from_controller() -> None:
    coordinator = _coordinator(default_duration=20)
    refreshed = SolemStatus(SolemMode.SINGLE_STATION_ACTIVE, True, 1200, "post-cmd")
    coordinator.client.status = refreshed
    updates = _record_coordinator_updates(coordinator)

    await coordinator.async_start_station(2)

    assert coordinator.client.commands == [bytes.fromhex("310512020004b0")]
    assert coordinator.client.reads == 1
    assert updates == [refreshed]
    # Active station preserved because the post-command status confirms
    # SINGLE_STATION_ACTIVE; UI switches no longer wait for next poll.
    assert coordinator.active_station == 2
    # Adaptive polling speeds up while watering.
    assert coordinator.update_interval == timedelta(seconds=30)


@pytest.mark.asyncio
async def test_start_station_with_explicit_duration_overrides_default() -> None:
    """Services pass an explicit duration; the default_duration number is ignored."""
    coordinator = _coordinator(default_duration=20)
    coordinator.client.status = SolemStatus(
        SolemMode.SINGLE_STATION_ACTIVE, True, 600, "post-cmd"
    )
    _record_coordinator_updates(coordinator)

    # 10 minutes -> 600 seconds -> hex 0258 in the payload tail.
    await coordinator.async_start_station(1, duration=10)

    assert coordinator.client.commands == [bytes.fromhex("3105120100" + "0258")]


@pytest.mark.asyncio
async def test_start_all_with_explicit_duration_overrides_default() -> None:
    coordinator = _coordinator(default_duration=20)
    coordinator.client.status = SolemStatus(
        SolemMode.ALL_STATIONS_ACTIVE, True, 900, "post-cmd"
    )
    _record_coordinator_updates(coordinator)

    # 15 minutes -> 900 seconds -> hex 0384.
    await coordinator.async_start_all(duration=15)

    assert coordinator.client.commands == [bytes.fromhex("31051100000384")]


@pytest.mark.asyncio
async def test_stop_refreshes_state_and_slows_polling_back_down() -> None:
    coordinator = _coordinator()
    coordinator.update_interval = timedelta(seconds=30)
    coordinator.active_station = 1
    coordinator.client.status = SolemStatus(SolemMode.IDLE, False, 0, "post-stop")
    updates = _record_coordinator_updates(coordinator)

    await coordinator.async_stop()

    assert coordinator.client.commands == [bytes.fromhex("31051500ff0000")]
    assert coordinator.active_station is None
    assert updates and updates[-1].mode is SolemMode.IDLE
    assert coordinator.update_interval == timedelta(seconds=600)


@pytest.mark.asyncio
async def test_polling_is_skipped_while_manual_command_is_pending() -> None:
    coordinator = _coordinator()
    coordinator._manual_command_pending = True

    status = await coordinator._async_update_data()

    assert status == SolemStatus(SolemMode.IDLE, False, 0, "initial")
    assert coordinator.client.reads == 0


@pytest.mark.asyncio
async def test_polling_marks_status_unknown_when_device_is_unavailable() -> None:
    coordinator = _coordinator(ble_device_available=False)

    status = await coordinator._async_update_data()

    assert status is None
    assert coordinator.client.reads == 0


@pytest.mark.asyncio
async def test_polling_switches_to_active_interval_when_watering() -> None:
    coordinator = _coordinator()
    coordinator.client.status = SolemStatus(
        SolemMode.SINGLE_STATION_ACTIVE, True, 600, "active"
    )

    await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(seconds=30)


@pytest.mark.asyncio
async def test_polling_returns_to_idle_interval_when_done() -> None:
    coordinator = _coordinator()
    coordinator.update_interval = timedelta(seconds=30)
    coordinator.client.status = SolemStatus(SolemMode.IDLE, False, 0, "idle")

    await coordinator._async_update_data()

    assert coordinator.update_interval == timedelta(seconds=600)


@pytest.mark.asyncio
async def test_manual_command_waiting_for_poll_blocks_new_polling() -> None:
    coordinator = _coordinator()
    await coordinator._ble_operation_lock.acquire()

    stop_task = asyncio.create_task(coordinator.async_stop())
    await asyncio.sleep(0)

    assert coordinator._manual_command_pending is True
    status = await coordinator._async_update_data()
    assert status == SolemStatus(SolemMode.IDLE, False, 0, "initial")
    assert coordinator.client.reads == 0

    coordinator._ble_operation_lock.release()
    await stop_task
    assert coordinator._manual_command_pending is False


@pytest.mark.asyncio
async def test_refresh_status_reads_real_status_and_updates_coordinator_data() -> None:
    coordinator = _coordinator()
    refreshed_status = SolemStatus(SolemMode.SINGLE_STATION_ACTIVE, True, 1194, "read")
    coordinator.client.status = refreshed_status
    updates = _record_coordinator_updates(coordinator)

    await coordinator.async_refresh_status()

    assert coordinator.client.reads == 1
    assert updates == [refreshed_status]
    assert coordinator.data == refreshed_status
    assert coordinator._manual_command_pending is False


@pytest.mark.asyncio
async def test_refresh_status_clears_pending_flag_when_device_is_unavailable() -> None:
    coordinator = _coordinator(ble_device_available=False)
    updates = _record_coordinator_updates(coordinator)

    await coordinator.async_refresh_status()

    assert coordinator._manual_command_pending is False
    assert coordinator.client.reads == 0
    assert updates == [None]


# ---------------------------------------------------------------------- #
# Bluetooth-derived state (RSSI)
# ---------------------------------------------------------------------- #


def test_set_rssi_notifies_listeners_only_on_change() -> None:
    coordinator = _coordinator()
    events: list[int | None] = []
    unsub = coordinator.async_add_bluetooth_state_listener(
        lambda: events.append(coordinator.rssi)
    )
    try:
        coordinator._set_rssi(-55)
        coordinator._set_rssi(-55)  # same value -> no extra notification
        coordinator._set_rssi(-60)
    finally:
        unsub()

    assert events == [-55, -60]
    assert coordinator.rssi == -60


def test_set_rssi_ignores_none() -> None:
    coordinator = _coordinator()
    coordinator._set_rssi(-50)
    coordinator._set_rssi(None)

    assert coordinator.rssi == -50


def test_remove_bluetooth_state_listener_stops_notifications() -> None:
    coordinator = _coordinator()
    events: list[None] = []
    unsub = coordinator.async_add_bluetooth_state_listener(lambda: events.append(None))
    unsub()

    coordinator._set_rssi(-55)
    assert events == []


# ---------------------------------------------------------------------- #
# Last-watering tracking
# ---------------------------------------------------------------------- #


def test_track_watering_records_cycle_on_active_to_idle_transition() -> None:
    coordinator = _coordinator()
    coordinator.active_station = 2

    coordinator._track_watering_transitions(
        SolemStatus(SolemMode.SINGLE_STATION_ACTIVE, True, 600, "active")
    )
    assert coordinator._watering_started_at is not None
    assert coordinator._watering_station == 2

    coordinator._track_watering_transitions(
        SolemStatus(SolemMode.IDLE, False, 0, "idle")
    )

    assert coordinator.last_watering_station == 2
    assert coordinator.last_watering_duration is not None
    assert coordinator.last_watering_duration >= 0
    assert coordinator.last_watering_time is not None
    assert coordinator._watering_started_at is None


def test_track_watering_ignores_idle_when_no_active_observed() -> None:
    coordinator = _coordinator()

    coordinator._track_watering_transitions(
        SolemStatus(SolemMode.IDLE, False, 0, "idle")
    )

    assert coordinator.last_watering_time is None
    assert coordinator.last_watering_duration is None
    assert coordinator.last_watering_station is None


def test_track_watering_handles_none_status() -> None:
    coordinator = _coordinator()
    coordinator._track_watering_transitions(None)
    assert coordinator.last_watering_time is None


# ---------------------------------------------------------------------- #
# Run-program API
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_program_sends_expected_payload_and_marks_active() -> None:
    coordinator = _coordinator()
    coordinator.client.status = SolemStatus(
        SolemMode.SINGLE_STATION_ACTIVE, True, 600, "active"
    )

    await coordinator.async_run_program(2)

    assert coordinator.client.commands == [bytes.fromhex("31051400020000")]
    assert coordinator.active_program == 2
    assert coordinator.active_station is None


@pytest.mark.asyncio
async def test_run_program_clears_marker_when_controller_stays_idle() -> None:
    coordinator = _coordinator()
    coordinator.client.status = SolemStatus(SolemMode.IDLE, False, 0, "idle")

    await coordinator.async_run_program(1)

    assert coordinator.active_program is None


@pytest.mark.asyncio
async def test_stop_clears_active_program_marker() -> None:
    coordinator = _coordinator()
    coordinator.active_program = 3
    coordinator.client.status = SolemStatus(SolemMode.IDLE, False, 0, "idle")

    await coordinator.async_stop()

    assert coordinator.active_program is None


# ---------------------------------------------------------------------- #
# Programs refresh
# ---------------------------------------------------------------------- #


def test_programs_refresh_due_when_never_fetched() -> None:
    coordinator = _coordinator()
    assert coordinator._programs_refresh_due() is True


def test_programs_refresh_not_due_right_after_refresh() -> None:
    from datetime import datetime, timezone

    coordinator = _coordinator()
    coordinator.programs = []
    coordinator.programs_last_refresh = datetime.now(tz=timezone.utc)
    assert coordinator._programs_refresh_due() is False


def test_programs_listener_unsubscribe_stops_notifications() -> None:
    coordinator = _coordinator()
    calls: list[None] = []
    unsub = coordinator.async_add_programs_listener(lambda: calls.append(None))
    coordinator._notify_programs_listeners()
    unsub()
    coordinator._notify_programs_listeners()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_start_station_clears_active_program_marker() -> None:
    coordinator = _coordinator()
    coordinator.active_program = 2
    coordinator.client.status = SolemStatus(
        SolemMode.SINGLE_STATION_ACTIVE, True, 600, "active"
    )

    await coordinator.async_start_station(1)

    assert coordinator.active_program is None
    assert coordinator.active_station == 1
