"""Tests for the BLE client command flow."""

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import pytest

from custom_components.another_solem_bluetooth_watering_controller import client as client_module
from custom_components.another_solem_bluetooth_watering_controller.client import (
    SolemBleClient,
    is_solem_device_name,
)
from custom_components.another_solem_bluetooth_watering_controller.const import WRITE_UUID
from custom_components.another_solem_bluetooth_watering_controller.protocol import (
    COMMIT_COMMAND,
    STATUS_COMMAND,
)


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


def _ephemeral_client(fake: FakeBleakClient) -> SolemBleClient:
    """Build a client configured for the legacy connect/disconnect-per-op flow."""
    return SolemBleClient(
        "AA:BB:CC",
        client_factory=lambda address, timeout: fake,
        keep_connected=False,
    )


def _persistent_client(fake: FakeBleakClient) -> SolemBleClient:
    """Build a client that keeps the BLE connection open between operations."""
    return SolemBleClient(
        "AA:BB:CC",
        client_factory=lambda address, timeout: fake,
        keep_connected=True,
        idle_timeout=0,  # disable scheduled idle disconnect for deterministic tests
    )


@pytest.mark.asyncio
async def test_send_command_writes_command_then_commit() -> None:
    fake = FakeBleakClient()
    client = _ephemeral_client(fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    assert fake.writes == [
        (WRITE_UUID, bytes.fromhex("31051500ff0000"), False),
        (WRITE_UUID, COMMIT_COMMAND, False),
    ]


@pytest.mark.asyncio
async def test_send_command_disconnects_after_writing_when_ephemeral() -> None:
    fake = FakeBleakClient()
    client = _ephemeral_client(fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    assert fake.disconnect_calls == 1
    assert fake.is_connected is False


@pytest.mark.asyncio
async def test_send_command_keeps_connection_open_in_persistent_mode() -> None:
    fake = FakeBleakClient()
    client = _persistent_client(fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    assert fake.disconnect_calls == 0
    assert fake.is_connected is True


@pytest.mark.asyncio
async def test_consecutive_operations_reuse_connection_in_persistent_mode() -> None:
    fake = FakeBleakClient()
    client = _persistent_client(fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))
    await client.send_command(bytes.fromhex("31051500ff0000"))

    # One connect for the first call, none for the second (already connected).
    assert fake.connect_calls == 1
    assert fake.disconnect_calls == 0


@pytest.mark.asyncio
async def test_disconnect_closes_connection() -> None:
    fake = FakeBleakClient()
    client = _ephemeral_client(fake)

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
        keep_connected=False,
    )

    await client.connect()

    assert targets == [(fake_device, 15)]


@pytest.mark.asyncio
async def test_connect_does_not_close_stale_connections_on_happy_path(monkeypatch) -> None:
    """We only close stale connections during recovery to avoid disrupting our own session."""
    fake_client = FakeBleakClient()
    fake_device = FakeBleDevice("C8:B9:61:D5:AA:7E")
    calls = []
    stale_closed_addresses: list[str] = []

    async def fake_establish_connection(client_class, device, name, **kwargs):
        calls.append((client_class, device, name, kwargs))
        fake_client.is_connected = True
        return fake_client

    async def fake_close_stale_connections_by_address(address):
        stale_closed_addresses.append(address)

    monkeypatch.setattr(client_module, "establish_connection", fake_establish_connection)
    monkeypatch.setattr(
        client_module,
        "close_stale_connections_by_address",
        fake_close_stale_connections_by_address,
    )

    client = SolemBleClient(
        "C8:B9:61:D5:AA:7E",
        ble_device=cast(Any, fake_device),
        keep_connected=False,
    )

    await client.connect()

    assert stale_closed_addresses == []
    assert len(calls) == 1
    client_class, device, name, kwargs = calls[0]
    assert client_class is client_module.BleakClientWithServiceCache
    assert device is fake_device
    assert name == "C8:B9:61:D5:AA:7E"
    assert kwargs["timeout"] == 15
    # A disconnected_callback must be registered so we invalidate the cached
    # client immediately on spontaneous disconnect.
    assert callable(kwargs["disconnected_callback"])


@pytest.mark.asyncio
async def test_connect_closes_stale_connections_on_recovery(monkeypatch) -> None:
    fake_client = FakeBleakClient()
    fake_device = FakeBleDevice("C8:B9:61:D5:AA:7E")
    stale_closed_addresses: list[str] = []

    async def fake_establish_connection(client_class, device, name, **kwargs):
        fake_client.is_connected = True
        return fake_client

    async def fake_close_stale_connections_by_address(address):
        stale_closed_addresses.append(address)

    monkeypatch.setattr(client_module, "establish_connection", fake_establish_connection)
    monkeypatch.setattr(
        client_module,
        "close_stale_connections_by_address",
        fake_close_stale_connections_by_address,
    )

    client = SolemBleClient(
        "C8:B9:61:D5:AA:7E",
        ble_device=cast(Any, fake_device),
        keep_connected=False,
    )

    await client.connect(force_close_stale=True)

    assert stale_closed_addresses == ["C8:B9:61:D5:AA:7E"]


@pytest.mark.asyncio
async def test_read_status_writes_only_status_frame_no_redundant_commit() -> None:
    """Status read should issue a single `3b 00` write (no doubled commit).

    The historical code wrote `STATUS_COMMAND` followed by `COMMIT_COMMAND`,
    which after the switch to `3b 00` would have meant sending the same
    frame twice. The current flow issues `3b 00` exactly once.
    """
    fake = FakeBleakClient()

    async def write_then_notify(uuid: str, data: bytes, response: bool = False) -> None:
        fake.writes.append((uuid, data, response))
        if data == STATUS_COMMAND and fake.notify_handler is not None:
            packet = bytes.fromhex("3210024000000000000000000000000000")
            fake.notify_handler("sender", packet)

    fake.write_gatt_char = write_then_notify  # type: ignore[assignment]

    client = _ephemeral_client(fake)

    status = await client.read_status()

    assert status.raw == "3210024000000000000000000000000000"
    write_uuids_and_data = [(uuid, data) for uuid, data, _ in fake.writes]
    assert write_uuids_and_data == [(WRITE_UUID, STATUS_COMMAND)]
    assert STATUS_COMMAND == COMMIT_COMMAND  # safety: by design, same bytes


@pytest.mark.asyncio
async def test_read_status_timeout_has_diagnostic_message(monkeypatch) -> None:
    fake = FakeBleakClient()
    client = _ephemeral_client(fake)

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(client_module.asyncio, "wait_for", fake_wait_for)

    with pytest.raises(TimeoutError, match="No status notification received from SOLEM controller"):
        await client.read_status()


@pytest.mark.asyncio
async def test_send_command_retries_once_on_recoverable_error() -> None:
    """A transient BleakError should trigger one automatic retry before bubbling up."""
    from bleak.exc import BleakError

    fake = FakeBleakClient()
    attempts = {"count": 0}

    original_write = fake.write_gatt_char

    async def flaky_write(uuid: str, data: bytes, response: bool = False) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise BleakError("transient")
        await original_write(uuid, data, response)

    fake.write_gatt_char = flaky_write  # type: ignore[assignment]

    client = _ephemeral_client(fake)

    await client.send_command(bytes.fromhex("31051500ff0000"))

    # Two attempts total: 1 failure + 1 successful retry.
    assert attempts["count"] >= 2
    assert fake.writes  # second attempt actually wrote
