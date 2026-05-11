"""Tests for the BLE client command flow."""

from dataclasses import dataclass
from typing import Any, cast

import pytest

from custom_components.another_solem_bluetooth_watering_controller.client import (
    SolemBleClient,
    is_solem_device_name,
)
from custom_components.another_solem_bluetooth_watering_controller.const import WRITE_UUID
from custom_components.another_solem_bluetooth_watering_controller.protocol import COMMIT_COMMAND


class FakeBleakClient:
    """Small fake for the subset of BleakClient behavior used by the integration."""

    def __init__(self) -> None:
        self.is_connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.writes: list[tuple[str, bytes, bool]] = []
        self.notify_uuid: str | None = None
        self.notify_handler = None

    async def connect(self) -> None:
        self.connect_calls += 1
        self.is_connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.is_connected = False

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        self.writes.append((uuid, data, response))

    async def start_notify(self, uuid: str, handler) -> None:
        self.notify_uuid = uuid
        self.notify_handler = handler

    async def stop_notify(self, uuid: str) -> None:
        self.notify_uuid = None


@dataclass(frozen=True)
class FakeBleDevice:
    """Fake BLEDevice-like object used to verify proxy-aware target selection."""

    address: str


@pytest.mark.asyncio
async def test_send_command_writes_command_then_commit() -> None:
    fake = FakeBleakClient()
    client = SolemBleClient("AA:BB:CC", client_factory=lambda address, timeout: fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    assert fake.writes == [
        (WRITE_UUID, bytes.fromhex("31051500ff0000"), False),
        (WRITE_UUID, COMMIT_COMMAND, False),
    ]


@pytest.mark.asyncio
async def test_send_command_disconnects_after_writing() -> None:
    fake = FakeBleakClient()
    client = SolemBleClient("AA:BB:CC", client_factory=lambda address, timeout: fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    assert fake.disconnect_calls == 1
    assert fake.is_connected is False


@pytest.mark.asyncio
async def test_disconnect_closes_connection() -> None:
    fake = FakeBleakClient()
    client = SolemBleClient("AA:BB:CC", client_factory=lambda address, timeout: fake)

    await client.connect()
    await client.disconnect()

    assert fake.is_connected is False


@pytest.mark.parametrize("name", ["BL1IP-D5AA7E", "BL2IP-D5AA7E", "BL4IP-D5AA7E", "BL6IP-D5AA7E"])
def test_device_name_prefixes_include_all_bl_ip_variants(name: str) -> None:
    assert is_solem_device_name(name) is True


@pytest.mark.asyncio
async def test_connect_uses_ha_resolved_ble_device_when_available() -> None:
    fake_client = FakeBleakClient()
    fake_device = FakeBleDevice("C8:B9:61:D5:AA:7E")
    targets = []

    def factory(target, timeout):
        targets.append((target, timeout))
        return fake_client

    client = SolemBleClient(
        "C8:B9:61:D5:AA:7E",
        timeout=15,
        ble_device=cast(Any, fake_device),
        client_factory=factory,
    )

    await client.connect()

    assert targets == [(fake_device, 15)]
