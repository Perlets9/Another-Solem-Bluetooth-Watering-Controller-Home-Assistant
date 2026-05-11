"""Async BLE client for SOLEM BL-IP controllers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEVICE_NAME_PREFIXES,
    NOTIFY_UUID,
    WRITE_UUID,
)
from .protocol import COMMIT_COMMAND, STATUS_COMMAND, SolemStatus, parse_status_notification

_LOGGER = logging.getLogger(__name__)

BluetoothTarget = str | BLEDevice
ClientFactory = Callable[[BluetoothTarget, float], Any]


def is_solem_device_name(name: str | None) -> bool:
    """Return whether a Bluetooth name looks like a SOLEM BL-IP controller."""
    return bool(name and name.upper().startswith(DEVICE_NAME_PREFIXES))


class SolemBleClient:
    """BLE client that sends SOLEM commands and reads status notifications."""

    def __init__(
        self,
        address: str,
        timeout: float = DEFAULT_BLUETOOTH_TIMEOUT,
        ble_device: BLEDevice | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.address = address
        self.timeout = timeout
        self.ble_device = ble_device
        self._client_factory = client_factory
        self._client: Any | None = None
        self._operation_lock = asyncio.Lock()
        self._notification_event = asyncio.Event()
        self._last_notification: bytes | None = None

    def set_ble_device(self, ble_device: BLEDevice | None) -> None:
        """Update the HA-resolved BLE device used for the next connection."""
        self.ble_device = ble_device

    @staticmethod
    async def discover(timeout: float = 10.0) -> list[BLEDevice]:
        """Discover likely SOLEM BL-IP devices."""
        devices = await BleakScanner.discover(timeout=timeout)
        return [
            device
            for device in devices
            if is_solem_device_name(device.name)
        ]

    async def connect(self) -> None:
        """Connect to the BLE device."""
        if self._client and self._client.is_connected:
            return
        if self.ble_device is not None and self._client_factory is None:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self.ble_device,
                self.address,
                timeout=self.timeout,
            )
            return
        factory = self._client_factory or (lambda target, timeout: BleakClient(target, timeout=timeout))
        self._client = factory(self.ble_device or self.address, self.timeout)
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    async def _write_command(self, command: bytes) -> None:
        """Write a command and commit frame on an existing connection."""
        await self._client.write_gatt_char(WRITE_UUID, command, response=False)
        await asyncio.sleep(0.1)
        await self._client.write_gatt_char(WRITE_UUID, COMMIT_COMMAND, response=False)

    async def send_command(self, command: bytes) -> None:
        """Send a command followed by the mandatory commit frame."""
        async with self._operation_lock:
            await self.connect()
            try:
                await self._write_command(command)
            finally:
                await self.disconnect()

    async def read_status(self) -> SolemStatus:
        """Poll the controller status using the non-intrusive ON command."""
        async with self._operation_lock:
            await self.connect()
            self._last_notification = None
            self._notification_event.clear()

            def _handler(_sender: str, data: bytes) -> None:
                if len(data) >= 15 and data[2] == 0x02:
                    self._last_notification = data
                    self._notification_event.set()

            try:
                await self._client.start_notify(NOTIFY_UUID, _handler)
                try:
                    await self._write_command(STATUS_COMMAND)
                    await asyncio.wait_for(self._notification_event.wait(), timeout=5)
                finally:
                    try:
                        await self._client.stop_notify(NOTIFY_UUID)
                    except Exception as err:
                        _LOGGER.debug("Failed to stop SOLEM notifications cleanly: %s", err)
            finally:
                await self.disconnect()

            if self._last_notification is None:
                raise TimeoutError("No status notification received from SOLEM controller")

            return parse_status_notification(self._last_notification)
