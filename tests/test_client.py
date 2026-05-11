"""Tests for the BLE client command flow."""

import pytest

from custom_components.another_solem_bluetooth_watering_controller.client import SolemBleClient
from custom_components.another_solem_bluetooth_watering_controller.const import WRITE_UUID
from custom_components.another_solem_bluetooth_watering_controller.protocol import COMMIT_COMMAND


class FakeBleakClient:
    """Small fake for the subset of BleakClient behavior used by the integration."""

    def __init__(self) -> None:
        self.is_connected = False
        self.writes: list[tuple[str, bytes, bool]] = []
        self.notify_uuid: str | None = None
        self.notify_handler = None

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        self.writes.append((uuid, data, response))

    async def start_notify(self, uuid: str, handler) -> None:
        self.notify_uuid = uuid
        self.notify_handler = handler

    async def stop_notify(self, uuid: str) -> None:
        self.notify_uuid = None


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
async def test_disconnect_closes_connection() -> None:
    fake = FakeBleakClient()
    client = SolemBleClient("AA:BB:CC", client_factory=lambda address, timeout: fake)

    await client.connect()
    await client.disconnect()

    assert fake.is_connected is False
