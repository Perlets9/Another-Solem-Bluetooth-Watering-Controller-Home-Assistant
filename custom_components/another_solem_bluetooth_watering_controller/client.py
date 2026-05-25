"""Async BLE client for SOLEM BL-IP controllers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)

from .const import (
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_CONNECTION_IDLE_TIMEOUT,
    DEFAULT_KEEP_CONNECTION,
    DEVICE_NAME_PREFIXES,
    NOTIFY_UUID,
    WRITE_UUID,
)
from .protocol import COMMIT_COMMAND, STATUS_COMMAND, SolemStatus, parse_status_notification

_LOGGER = logging.getLogger(__name__)

BluetoothTarget = str | BLEDevice
ClientFactory = Callable[[BluetoothTarget, float], Any]

# Errors that warrant a fresh connection attempt (close stale + reconnect).
_RECOVERABLE_ERRORS: tuple[type[BaseException], ...] = (
    BleakError,
    EOFError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)

# Number of attempts per BLE operation. 1 normal try + 1 recovery retry.
_MAX_ATTEMPTS = 2
# Brief pause between attempts to let the BLE stack settle.
_RETRY_BACKOFF_SECONDS = 0.5


def is_solem_device_name(name: str | None) -> bool:
    """Return whether a Bluetooth name looks like a SOLEM BL-IP controller."""
    return bool(name and name.upper().startswith(DEVICE_NAME_PREFIXES))


class SolemBleClient:
    """BLE client that sends SOLEM commands and reads status notifications.

    The client keeps the BLE connection open between operations by default to
    minimize the connect/disconnect churn on battery-powered controllers and
    flaky Bluetooth proxies. After ``idle_timeout`` seconds without activity
    the connection is closed automatically. Each operation is retried once on
    recoverable errors, with a forced stale-connection cleanup between
    attempts.
    """

    def __init__(
        self,
        address: str,
        timeout: float = DEFAULT_BLUETOOTH_TIMEOUT,
        ble_device: BLEDevice | None = None,
        client_factory: ClientFactory | None = None,
        keep_connected: bool = DEFAULT_KEEP_CONNECTION,
        idle_timeout: float = DEFAULT_CONNECTION_IDLE_TIMEOUT,
    ) -> None:
        self.address = address
        self.timeout = timeout
        self.ble_device = ble_device
        self.keep_connected = keep_connected
        self.idle_timeout = idle_timeout
        self._client_factory = client_factory
        self._client: Any | None = None
        self._operation_lock = asyncio.Lock()
        self._notification_event = asyncio.Event()
        self._last_notification: bytes | None = None
        self._disconnect_handle: asyncio.TimerHandle | None = None

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

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self, *, force_close_stale: bool = False) -> None:
        """Connect to the BLE device.

        ``force_close_stale`` is used by the retry path to clear lingering
        connections held by the OS or a Bluetooth proxy after a failure.
        On the happy path we skip ``close_stale_connections_by_address`` to
        avoid disrupting our own freshly-established session.
        """
        self._cancel_idle_disconnect()
        if self._client and self._client.is_connected:
            return
        if self.ble_device is not None and self._client_factory is None:
            if force_close_stale:
                await close_stale_connections_by_address(self.address)
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self.ble_device,
                self.address,
                disconnected_callback=self._handle_disconnect,
                timeout=self.timeout,
            )
            return
        factory = self._client_factory or (lambda target, timeout: BleakClient(target, timeout=timeout))
        self._client = factory(self.ble_device or self.address, self.timeout)
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect from the BLE device on caller request (e.g. reset button)."""
        self._cancel_idle_disconnect()
        await self._safe_disconnect()

    async def reset(self) -> None:
        """Forcefully tear down the BLE session and clear OS-level stale handles.

        Stronger than :meth:`disconnect`: also asks bleak-retry-connector to
        clean up any lingering connections held by the OS / Bluetooth proxy
        for this address, so the next operation can rebuild the session from
        a known-clean state. Useful as a diagnostic recovery button.
        """
        async with self._operation_lock:
            self._cancel_idle_disconnect()
            await self._safe_disconnect()
            try:
                await close_stale_connections_by_address(self.address)
            except Exception as err:  # noqa: BLE001 - best-effort cleanup
                _LOGGER.debug("close_stale_connections_by_address failed: %s", err)

    async def _safe_disconnect(self) -> None:
        """Disconnect best-effort, swallowing transport errors."""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception as err:  # noqa: BLE001 - best-effort cleanup
            _LOGGER.debug("Error during SOLEM disconnect: %s", err)

    def _handle_disconnect(self, _client: Any) -> None:
        """Bleak disconnected_callback - clear cached client so we reconnect."""
        _LOGGER.debug("SOLEM controller %s reported disconnected", self.address)
        self._client = None
        self._cancel_idle_disconnect()

    def _cancel_idle_disconnect(self) -> None:
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
            self._disconnect_handle = None

    def _arm_idle_disconnect(self) -> None:
        """Schedule an idle disconnect if persistent connections are enabled."""
        self._cancel_idle_disconnect()
        if not self.keep_connected or self.idle_timeout <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._disconnect_handle = loop.call_later(
            self.idle_timeout,
            lambda: asyncio.ensure_future(self._idle_disconnect()),
        )

    async def _idle_disconnect(self) -> None:
        async with self._operation_lock:
            self._disconnect_handle = None
            if self._client and self._client.is_connected:
                _LOGGER.debug(
                    "Closing idle SOLEM connection to %s after %ss",
                    self.address,
                    self.idle_timeout,
                )
                await self._safe_disconnect()

    # ------------------------------------------------------------------ #
    # GATT operations
    # ------------------------------------------------------------------ #

    async def _write_command(self, command: bytes) -> None:
        """Write a command and commit frame on an existing connection."""
        await self._client.write_gatt_char(WRITE_UUID, command, response=False)
        await asyncio.sleep(0.1)
        await self._client.write_gatt_char(WRITE_UUID, COMMIT_COMMAND, response=False)

    async def send_command(self, command: bytes) -> None:
        """Send a command followed by the mandatory commit frame."""
        async with self._operation_lock:
            await self._execute(lambda: self._write_command(command), op_name="send_command")

    async def read_status(self) -> SolemStatus:
        """Poll the controller status using the non-intrusive ON command."""
        async with self._operation_lock:
            return await self._execute(self._do_read_status, op_name="read_status")

    async def _do_read_status(self) -> SolemStatus:
        self._last_notification = None
        self._notification_event.clear()

        def _handler(_sender: str, data: bytes) -> None:
            if len(data) >= 15 and data[2] == 0x02:
                self._last_notification = data
                self._notification_event.set()

        await self._client.start_notify(NOTIFY_UUID, _handler)
        try:
            await self._write_command(STATUS_COMMAND)
            try:
                await asyncio.wait_for(self._notification_event.wait(), timeout=self.timeout)
            except asyncio.TimeoutError as err:
                raise TimeoutError(
                    "No status notification received from SOLEM controller"
                ) from err
        finally:
            try:
                await self._client.stop_notify(NOTIFY_UUID)
            except Exception as err:  # noqa: BLE001 - notify teardown is best-effort
                _LOGGER.debug("Failed to stop SOLEM notifications cleanly: %s", err)

        if self._last_notification is None:
            raise TimeoutError("No status notification received from SOLEM controller")

        return parse_status_notification(self._last_notification)

    # ------------------------------------------------------------------ #
    # Execution wrapper: connect + operation + retry + disconnect policy
    # ------------------------------------------------------------------ #

    async def _execute(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        op_name: str,
    ) -> Any:
        """Run an operation with one recovery retry on recoverable errors."""
        last_error: BaseException | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                await self.connect(force_close_stale=attempt > 0)
                result = await operation()
            except _RECOVERABLE_ERRORS as err:
                last_error = err
                _LOGGER.debug(
                    "SOLEM %s attempt %d/%d failed: %s",
                    op_name,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    err,
                )
                await self._safe_disconnect()
                if attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            except BaseException:
                # Non-recoverable error: tear down the session and bubble up.
                await self._safe_disconnect()
                raise

            if self.keep_connected:
                self._arm_idle_disconnect()
            else:
                await self._safe_disconnect()
            return result

        assert last_error is not None
        raise last_error
