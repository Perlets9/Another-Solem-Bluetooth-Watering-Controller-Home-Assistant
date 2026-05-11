"""Tests for SOLEM BL-IP coordinator helpers."""

import asyncio
from datetime import timedelta
from types import MethodType

import pytest

from custom_components.another_solem_bluetooth_watering_controller.const import (
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
)
from custom_components.another_solem_bluetooth_watering_controller.coordinator import (
    SolemCoordinator,
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


def _coordinator(default_duration: int = 20, ble_device_available: bool = True) -> SolemCoordinator:
    coordinator = SolemCoordinator.__new__(SolemCoordinator)
    coordinator.default_duration = default_duration
    coordinator.active_station = None
    coordinator.client = FakeClient()
    coordinator.data = SolemStatus(SolemMode.IDLE, False, 0, "initial")
    coordinator._ble_operation_lock = asyncio.Lock()
    coordinator._manual_command_pending = False
    coordinator._async_set_latest_ble_device = MethodType(
        lambda self: ble_device_available,
        coordinator,
    )
    coordinator.async_set_updated_data = MethodType(
        lambda self, data: (_ for _ in ()).throw(
            AssertionError("manual commands must not update assumed state locally")
        ),
        coordinator,
    )

    async def _unexpected_refresh(self):
        raise AssertionError("manual commands must not request an immediate BLE refresh")

    coordinator.async_request_refresh = MethodType(_unexpected_refresh, coordinator)
    return coordinator


def _record_coordinator_updates(coordinator: SolemCoordinator) -> list[SolemStatus | None]:
    updates: list[SolemStatus | None] = []

    def _record_update(self, data):
        updates.append(data)
        self.data = data

    coordinator.async_set_updated_data = MethodType(_record_update, coordinator)
    return updates


def test_polling_update_interval_uses_configured_interval_when_enabled() -> None:
    assert polling_update_interval({CONF_POLLING_ENABLED: True, CONF_POLL_INTERVAL: 15}) == timedelta(
        seconds=15
    )


def test_polling_update_interval_is_none_when_disabled() -> None:
    assert polling_update_interval({CONF_POLLING_ENABLED: False, CONF_POLL_INTERVAL: 15}) is None


@pytest.mark.asyncio
async def test_start_station_sends_command_without_assuming_state_or_refreshing() -> None:
    coordinator = _coordinator(default_duration=20)

    await coordinator.async_start_station(2)

    assert coordinator.active_station == 2
    assert coordinator.data == SolemStatus(SolemMode.IDLE, False, 0, "initial")
    assert coordinator.client.commands == [bytes.fromhex("310512020004b0")]


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
