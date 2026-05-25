"""Coordinator for SOLEM BL-IP state polling and commands."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SolemBleClient
from .const import (
    CONF_ACTIVE_POLL_INTERVAL,
    CONF_ADDRESS,
    CONF_BLUETOOTH_TIMEOUT,
    CONF_CONNECTION_IDLE_TIMEOUT,
    CONF_DEFAULT_DURATION,
    CONF_IDLE_POLL_INTERVAL,
    CONF_KEEP_CONNECTION,
    CONF_POLL_INTERVAL,
    CONF_POLLING_ENABLED,
    CONF_STATION_COUNT,
    DEFAULT_ACTIVE_POLL_INTERVAL,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_CONNECTION_IDLE_TIMEOUT,
    DEFAULT_DURATION,
    DEFAULT_IDLE_POLL_INTERVAL,
    DEFAULT_KEEP_CONNECTION,
    DEFAULT_POLLING_ENABLED,
    DOMAIN,
)
from .protocol import (
    SolemMode,
    SolemStatus,
    build_all_stations_command,
    build_station_command,
    stop_command,
)

_LOGGER = logging.getLogger(__name__)


def _int_option(config: dict, key: str, fallback: int) -> int:
    """Read an integer option falling back to ``fallback`` on any error."""
    try:
        return int(config.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def idle_poll_interval(config: dict) -> timedelta | None:
    """Return the polling interval to use when the controller is idle.

    Honors the legacy ``CONF_POLL_INTERVAL`` if the new
    ``CONF_IDLE_POLL_INTERVAL`` is not present (backward compatibility with
    config entries created before adaptive polling).
    """
    if not config.get(CONF_POLLING_ENABLED, DEFAULT_POLLING_ENABLED):
        return None
    if CONF_IDLE_POLL_INTERVAL in config:
        seconds = _int_option(config, CONF_IDLE_POLL_INTERVAL, DEFAULT_IDLE_POLL_INTERVAL)
    elif CONF_POLL_INTERVAL in config:
        seconds = _int_option(config, CONF_POLL_INTERVAL, DEFAULT_IDLE_POLL_INTERVAL)
    else:
        seconds = DEFAULT_IDLE_POLL_INTERVAL
    if seconds <= 0:
        return None
    return timedelta(seconds=seconds)


def active_poll_interval(config: dict) -> timedelta:
    """Return the polling interval to use while the controller is watering."""
    seconds = _int_option(
        config, CONF_ACTIVE_POLL_INTERVAL, DEFAULT_ACTIVE_POLL_INTERVAL
    )
    if seconds <= 0:
        seconds = DEFAULT_ACTIVE_POLL_INTERVAL
    return timedelta(seconds=seconds)


# Backward-compatible alias used by existing tests.
def polling_update_interval(config: dict) -> timedelta | None:
    """Return the initial coordinator update interval (idle baseline)."""
    return idle_poll_interval(config)


class SolemCoordinator(DataUpdateCoordinator[SolemStatus | None]):
    """Poll status and send manual commands to the SOLEM controller."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.station_count = entry.data[CONF_STATION_COUNT]
        current_config: dict = {**entry.data, **entry.options}
        self.default_duration = int(
            entry.options.get(CONF_DEFAULT_DURATION, entry.data.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION))
        )
        timeout = _int_option(current_config, CONF_BLUETOOTH_TIMEOUT, DEFAULT_BLUETOOTH_TIMEOUT)
        keep_connection = bool(
            current_config.get(CONF_KEEP_CONNECTION, DEFAULT_KEEP_CONNECTION)
        )
        idle_timeout = _int_option(
            current_config, CONF_CONNECTION_IDLE_TIMEOUT, DEFAULT_CONNECTION_IDLE_TIMEOUT
        )
        self.client = SolemBleClient(
            self.address,
            timeout=timeout,
            keep_connected=keep_connection,
            idle_timeout=idle_timeout,
        )
        self.active_station: int | None = None
        self._ble_operation_lock = asyncio.Lock()
        self._manual_command_pending = False
        self._idle_interval = idle_poll_interval(current_config)
        self._active_interval = active_poll_interval(current_config)
        # Cached BLEDevice updated from passive Bluetooth advertisements; used
        # as a fallback when HA's manager momentarily forgets the device (the
        # BL-IP advertises infrequently, so async_ble_device_from_address can
        # return None even when a recent connection is still feasible).
        self._cached_ble_device = None
        self._cancel_bluetooth_listener = None
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=self._idle_interval,
            always_update=False,
        )

    def async_start_bluetooth_listener(self) -> None:
        """Subscribe to advertisements for our address to keep a BLEDevice cached."""
        if self._cancel_bluetooth_listener is not None:
            return
        from homeassistant.components.bluetooth import (
            BluetoothScanningMode,
            async_ble_device_from_address,
            async_last_service_info,
            async_register_callback,
        )
        from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher

        # Seed the cache from whatever HA already knows so we don't have to
        # wait for the next advertisement to attempt a first connection.
        info = async_last_service_info(self.hass, self.address, connectable=True)
        if info is not None:
            self._cached_ble_device = info.device
        else:
            self._cached_ble_device = async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )

        self._cancel_bluetooth_listener = async_register_callback(
            self.hass,
            self._async_handle_bluetooth_event,
            BluetoothCallbackMatcher(address=self.address, connectable=True),
            BluetoothScanningMode.PASSIVE,
        )

    @callback
    def _async_handle_bluetooth_event(self, service_info, change) -> None:
        """Cache the freshest BLEDevice we've seen for this address."""
        if service_info.connectable:
            self._cached_ble_device = service_info.device

    def async_stop_bluetooth_listener(self) -> None:
        """Cancel the advertisement subscription on unload."""
        if self._cancel_bluetooth_listener is not None:
            self._cancel_bluetooth_listener()
            self._cancel_bluetooth_listener = None

    def _async_set_latest_ble_device(self) -> bool:
        """Refresh the client target, falling back to the cached BLEDevice.

        ``async_ble_device_from_address`` only returns a device that HA has
        seen advertising recently. Battery-powered BL-IP controllers can be
        silent for minutes at a time, so we also keep our own cache populated
        via the passive callback registered in ``async_start_bluetooth_listener``.
        Trying with a slightly stale BLEDevice is far better than refusing the
        operation: bleak-retry-connector will reconnect through whichever
        proxy/adapter last knew about the device.
        """
        from homeassistant.components.bluetooth import (
            async_ble_device_from_address,
            async_last_service_info,
        )

        ble_device = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            info = async_last_service_info(self.hass, self.address, connectable=True)
            if info is not None:
                ble_device = info.device
        if ble_device is None:
            ble_device = self._cached_ble_device
        else:
            self._cached_ble_device = ble_device
        self.client.set_ble_device(ble_device)
        return ble_device is not None

    def _apply_adaptive_interval(self, status: SolemStatus | None) -> None:
        """Switch between active/idle polling cadence based on watering state.

        While watering, we want timely UI updates; when idle we drop to the
        long interval (or pause entirely) to spare the controller battery and
        avoid pointless BLE traffic.
        """
        if status is not None and status.active:
            new_interval = self._active_interval
        else:
            new_interval = self._idle_interval
        if new_interval != self.update_interval:
            _LOGGER.debug(
                "Adaptive polling: switching SOLEM interval %s -> %s",
                self.update_interval,
                new_interval,
            )
            self.update_interval = new_interval

    async def _async_update_data(self) -> SolemStatus | None:
        if self._manual_command_pending:
            if self.data is not None:
                return self.data
            raise UpdateFailed("Skipping SOLEM polling while a manual command is pending")

        async with self._ble_operation_lock:
            if self._manual_command_pending:
                if self.data is not None:
                    return self.data
                raise UpdateFailed("Skipping SOLEM polling while a manual command is pending")

            try:
                status = await self._async_read_status()
            except UpdateFailed as err:
                _LOGGER.debug("SOLEM status unavailable during polling: %s", err)
                self._apply_adaptive_interval(None)
                return None
            self._apply_adaptive_interval(status)
            return status

    async def _async_read_status(self) -> SolemStatus:
        """Read status from the controller."""
        try:
            if not self._async_set_latest_ble_device():
                raise UpdateFailed(
                    "SOLEM controller has not been seen by Home Assistant Bluetooth yet"
                )
            status = await self.client.read_status()
            if status.mode is not SolemMode.SINGLE_STATION_ACTIVE:
                self.active_station = None
            return status
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Unable to read SOLEM status: {err}") from err

    async def _async_send_command(self, command: bytes, action: str) -> SolemStatus | None:
        """Send a SOLEM command and refresh state from the controller.

        Returns the post-command status when it could be read so callers can
        derive accurate UI state (e.g. switch ``is_on``) without waiting for
        the next scheduled poll, which kept users tapping the switch twice
        and doubled the BLE workload.
        """
        self._manual_command_pending = True
        try:
            async with self._ble_operation_lock:
                if not self._async_set_latest_ble_device():
                    raise HomeAssistantError(
                        "SOLEM controller has not been seen by Home Assistant Bluetooth "
                        "recently. Ensure the BL-IP is powered and in range of a Bluetooth "
                        "adapter or ESPHome proxy."
                    )
                try:
                    await self.client.send_command(command)
                except Exception as err:
                    raise HomeAssistantError(f"Unable to {action}: {err}") from err

                # Refresh status while holding the lock so the persistent BLE
                # session is reused; tolerate read failures (the command was
                # accepted, the state will be reconciled by the next poll).
                try:
                    status = await self._async_read_status()
                except UpdateFailed as err:
                    _LOGGER.debug(
                        "SOLEM status refresh after %s failed: %s", action, err
                    )
                    return None
                self.async_set_updated_data(status)
                self._apply_adaptive_interval(status)
                return status
        finally:
            self._manual_command_pending = False

    async def async_start_station(self, station: int, duration: int | None = None) -> None:
        """Start one station for ``duration`` minutes (defaults to the configured value).

        Switches always omit ``duration`` and rely on the per-coordinator
        default. Service calls pass ``duration`` explicitly so automations
        can water each station for a different time without juggling the
        global ``Watering Duration`` number.
        """
        minutes = self.default_duration if duration is None else int(duration)
        # Tentatively remember the commanded station so the post-command
        # refresh maps SINGLE_STATION_ACTIVE -> this station in the UI.
        self.active_station = station
        status = await self._async_send_command(
            build_station_command(station, minutes),
            f"start SOLEM station {station} for {minutes} min",
        )
        if status is not None and status.mode is not SolemMode.SINGLE_STATION_ACTIVE:
            self.active_station = None

    async def async_start_all(self, duration: int | None = None) -> None:
        """Start all stations for ``duration`` minutes (defaults to configured)."""
        minutes = self.default_duration if duration is None else int(duration)
        self.active_station = None
        await self._async_send_command(
            build_all_stations_command(minutes),
            f"start all SOLEM stations for {minutes} min",
        )

    async def async_stop(self) -> None:
        """Stop manual watering."""
        await self._async_send_command(stop_command(), "stop SOLEM watering")
        self.active_station = None

    async def async_refresh_status(self) -> None:
        """Manually refresh the controller status through the BLE operation queue."""
        self._manual_command_pending = True
        try:
            async with self._ble_operation_lock:
                try:
                    status = await self._async_read_status()
                except Exception as err:
                    _LOGGER.debug("SOLEM status unavailable during manual refresh: %s", err)
                    status = None
                self.async_set_updated_data(status)
                self._apply_adaptive_interval(status)
        finally:
            self._manual_command_pending = False

    async def async_reset_connection(self) -> None:
        """Reset the local BLE client connection.

        Disconnects, asks the OS/proxy to drop any stale connections for the
        address, and invalidates the cached BLEDevice so the next poll picks
        up a fresh one from HA Bluetooth.
        """
        await self.client.reset()
        self._cached_ble_device = None
        _LOGGER.debug("SOLEM BLE connection reset by user request")
