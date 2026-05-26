"""Coordinator for SOLEM BL-IP state polling and commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
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
    CONF_PROGRAMS_REFRESH_INTERVAL,
    CONF_STATION_COUNT,
    DEFAULT_ACTIVE_POLL_INTERVAL,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_CONNECTION_IDLE_TIMEOUT,
    DEFAULT_DURATION,
    DEFAULT_IDLE_POLL_INTERVAL,
    DEFAULT_KEEP_CONNECTION,
    DEFAULT_POLLING_ENABLED,
    DEFAULT_PROGRAMS_REFRESH_INTERVAL,
    DOMAIN,
    MIN_PROGRAMS_REFRESH_INTERVAL,
)
from .programs import (
    Program,
    apply_program_changes,
    encode_program,
    unknown_diff_locations,
)
from .protocol import (
    SolemDeviceInfo,
    SolemMode,
    SolemStatus,
    build_all_stations_command,
    build_run_program_command,
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
        # Program currently being executed via "Run Program" (1..3). Reset
        # to None whenever a manual station/all-stations command or a stop
        # is issued, so the dedicated switch correctly turns off.
        self.active_program: int | None = None
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
        # Latest RSSI seen on a connectable advertisement. Updated by the
        # passive Bluetooth listener and consumed by the RSSI sensor without
        # going through the polling cycle.
        self.rssi: int | None = None
        # Listeners notified when passive Bluetooth-derived state (RSSI)
        # changes, so dedicated entities can push updates without a poll.
        self._bluetooth_state_listeners: set[Callable[[], None]] = set()
        # Cached device identity (MAC, model, firmware). Read once after the
        # first successful connection and reused by every entity's DeviceInfo
        # block. ``None`` until populated; entities still render correctly
        # with the defaults baked into entity.py in that case.
        self.device_info: SolemDeviceInfo | None = None
        # Last-watering tracking. ``_watering_started_at`` is set the moment
        # we first see ``active=True`` in any status read; on the next
        # ``active=False`` we close the cycle and publish the totals below.
        self._watering_started_at: datetime | None = None
        self._watering_station: int | None = None
        self.last_watering_time: datetime | None = None
        self.last_watering_station: int | None = None
        self.last_watering_duration: int | None = None
        # Program slot snapshots. Populated on demand by
        # ``async_refresh_programs`` (initial bootstrap + scheduled refresh
        # every ``_programs_refresh_interval``).
        self.programs: list[Program] | None = None
        self.programs_last_refresh: datetime | None = None
        programs_refresh = _int_option(
            current_config,
            CONF_PROGRAMS_REFRESH_INTERVAL,
            DEFAULT_PROGRAMS_REFRESH_INTERVAL,
        )
        if programs_refresh < MIN_PROGRAMS_REFRESH_INTERVAL:
            programs_refresh = MIN_PROGRAMS_REFRESH_INTERVAL
        self._programs_refresh_interval = timedelta(seconds=programs_refresh)
        # Listeners (typically program-aware entities) get notified after
        # every successful programs refresh so they can re-render.
        self._programs_listeners: set[Callable[[], None]] = set()
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
            self._set_rssi(getattr(info, "rssi", None))
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
            self._set_rssi(getattr(service_info, "rssi", None))

    def _set_rssi(self, rssi: int | None) -> None:
        """Update the cached RSSI and notify subscribers if it changed.

        Notifications are emitted on every fresh advertisement (RSSI typically
        varies on every packet), letting the dedicated sensor push updates
        without going through the polling pipeline.
        """
        if rssi is None:
            return
        if rssi == self.rssi:
            return
        self.rssi = rssi
        for listener in tuple(self._bluetooth_state_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - listener errors must not break BT cb
                _LOGGER.exception("SOLEM Bluetooth listener raised")

    def async_add_bluetooth_state_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired on passive Bluetooth state updates."""
        self._bluetooth_state_listeners.add(listener)
        return lambda: self._bluetooth_state_listeners.discard(listener)

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

    def _track_watering_transitions(self, status: SolemStatus | None) -> None:
        """Detect inactive->active and active->inactive transitions.

        On the rising edge we remember when watering started and which
        station was running; on the falling edge we publish the closed
        cycle as ``last_watering_*`` so the dedicated sensors can render
        it. Only "real" cycles get published (i.e. we ignore the falling
        edge if we never saw a rising one, which would mean the controller
        already finished before HA observed it).
        """
        if status is None:
            return
        if status.active:
            if self._watering_started_at is None:
                self._watering_started_at = dt_util.utcnow()
            self._watering_station = self.active_station
            return
        if self._watering_started_at is None:
            return
        ended_at = dt_util.utcnow()
        duration = max(int((ended_at - self._watering_started_at).total_seconds()), 0)
        self.last_watering_time = ended_at
        self.last_watering_station = self._watering_station
        self.last_watering_duration = duration
        self._watering_started_at = None
        self._watering_station = None

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
            self._track_watering_transitions(status)
            self._apply_adaptive_interval(status)
            should_refresh_programs = self._programs_refresh_due()

        if should_refresh_programs:
            # Run outside the lock as a background task: the programs read
            # acquires its own lock and we don't want to hold this one for
            # the ~2s the 84-frame dump takes.
            hass = getattr(self, "hass", None)
            if hass is not None:
                hass.async_create_task(self.async_refresh_programs())
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
                self._track_watering_transitions(status)
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
        self.active_program = None
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
        self.active_program = None
        await self._async_send_command(
            build_all_stations_command(minutes),
            f"start all SOLEM stations for {minutes} min",
        )

    async def async_run_program(self, program: int) -> None:
        """Start the given configured program on demand.

        Mirrors MySolem's "Run program" button. The controller will run the
        program's stations one after the other, honoring the per-station
        durations stored on the device itself; we don't need to pass any
        timing here.
        """
        self.active_station = None
        self.active_program = int(program)
        status = await self._async_send_command(
            build_run_program_command(program),
            f"run SOLEM program {program}",
        )
        if status is not None and not status.active:
            # If the controller reports back idle, the program likely had no
            # stations assigned (or finished immediately). Clear the marker.
            self.active_program = None

    async def async_stop(self) -> None:
        """Stop manual watering."""
        await self._async_send_command(stop_command(), "stop SOLEM watering")
        self.active_station = None
        self.active_program = None

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
                self._track_watering_transitions(status)
                self.async_set_updated_data(status)
                self._apply_adaptive_interval(status)
        finally:
            self._manual_command_pending = False

    def async_add_programs_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback fired after every programs refresh."""
        self._programs_listeners.add(listener)
        return lambda: self._programs_listeners.discard(listener)

    def _notify_programs_listeners(self) -> None:
        for listener in tuple(self._programs_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - listener errors must not break flow
                _LOGGER.exception("SOLEM programs listener raised")

    def _programs_refresh_due(self) -> bool:
        """Whether the cached programs are stale enough to warrant a re-read."""
        if self.programs is None:
            return True
        if self.programs_last_refresh is None:
            return True
        return (
            dt_util.utcnow() - self.programs_last_refresh
            >= self._programs_refresh_interval
        )

    async def async_refresh_programs(self) -> list[Program] | None:
        """Re-read the 12 program slots from the controller.

        Failure-tolerant: a missing read leaves the cached snapshot intact
        so dashboards don't blank out on a transient BLE hiccup. Skipped
        entirely while a manual command is pending to avoid stepping on
        active station/run-program traffic.
        """
        if self._manual_command_pending:
            _LOGGER.debug(
                "Skipping SOLEM programs refresh: manual command in flight"
            )
            return self.programs
        async with self._ble_operation_lock:
            if not self._async_set_latest_ble_device():
                _LOGGER.debug(
                    "Skipping SOLEM programs refresh: controller not in range yet"
                )
                return self.programs
            try:
                programs = await self.client.read_programs()
            except Exception as err:  # noqa: BLE001 - best-effort read
                _LOGGER.debug("SOLEM programs read failed: %s", err)
                return self.programs
        self.programs = programs
        self.programs_last_refresh = dt_util.utcnow()
        self._notify_programs_listeners()
        return programs

    async def async_configure_program(self, program_index: int, **changes) -> Program:
        """Read-modify-write a program slot with safety checks.

        Workflow:
        1. Refresh cached programs (fresh dump) so we modify the current
           bytes-on-the-wire, not a stale snapshot.
        2. Build the modified :class:`Program` via :func:`apply_program_changes`.
        3. Re-encode it and compare to the original payload. If any byte
           outside the integration's known-writable offsets has changed,
           abort instead of overwriting reserved bytes blindly.
        4. Write all 7 rows + commit, then re-read to confirm.
        """
        if not 1 <= program_index <= 3:
            raise HomeAssistantError(
                f"Program index must be 1, 2 or 3 (got {program_index})"
            )
        await self.async_refresh_programs()
        programs = self.programs or []
        idx = program_index - 1
        if idx >= len(programs):
            raise HomeAssistantError(
                f"Program {program_index} is not available on this controller"
            )
        original = programs[idx]
        if not original.raw_rows:
            raise HomeAssistantError(
                "No raw program payload cached; refresh the integration and retry"
            )
        modified = apply_program_changes(original, **changes)
        new_rows = encode_program(modified)
        bad = unknown_diff_locations(original.raw_rows, new_rows)
        if bad:
            raise HomeAssistantError(
                "Refusing to write SOLEM program: the change would touch "
                f"{len(bad)} reserved bytes (row,offset = {bad[:5]}...). "
                "This protects you from corrupting the controller config."
            )
        async with self._ble_operation_lock:
            if not self._async_set_latest_ble_device():
                raise HomeAssistantError(
                    "SOLEM controller not reachable; try again later"
                )
            try:
                await self.client.write_program(original.slot, new_rows)
            except Exception as err:  # noqa: BLE001 - surface BLE errors to the UI
                raise HomeAssistantError(
                    f"Unable to configure SOLEM program {program_index}: {err}"
                ) from err
        # Re-read so cached state and entities reflect the new config.
        await self.async_refresh_programs()
        return modified

    async def async_refresh_device_info(self) -> SolemDeviceInfo | None:
        """Read device identity once, caching it on the coordinator.

        Failure-tolerant: a missing device-info read must not block setup
        (entities fall back to the static defaults in entity.py until the
        controller becomes reachable). Skips the read if we already have
        a populated cache.
        """
        if self.device_info is not None and self.device_info.mac:
            return self.device_info
        async with self._ble_operation_lock:
            if not self._async_set_latest_ble_device():
                _LOGGER.debug(
                    "Skipping SOLEM device-info read: controller not in range yet"
                )
                return None
            try:
                info = await self.client.read_device_info()
            except Exception as err:  # noqa: BLE001 - best-effort metadata
                _LOGGER.debug("SOLEM device-info read failed: %s", err)
                return None
        if info.mac or info.device_name:
            self.device_info = info
        return self.device_info

    async def async_reset_connection(self) -> None:
        """Reset the local BLE client connection.

        Disconnects, asks the OS/proxy to drop any stale connections for the
        address, and invalidates the cached BLEDevice so the next poll picks
        up a fresh one from HA Bluetooth.
        """
        await self.client.reset()
        self._cached_ble_device = None
        _LOGGER.debug("SOLEM BLE connection reset by user request")
