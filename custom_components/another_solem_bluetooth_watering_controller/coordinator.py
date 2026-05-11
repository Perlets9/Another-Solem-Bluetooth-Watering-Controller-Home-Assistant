"""Coordinator for SOLEM BL-IP state polling and commands."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SolemBleClient
from .const import (
    CONF_ADDRESS,
    CONF_BLUETOOTH_TIMEOUT,
    CONF_DEFAULT_DURATION,
    CONF_POLL_INTERVAL,
    CONF_STATION_COUNT,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_DURATION,
    DEFAULT_POLL_INTERVAL,
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


class SolemCoordinator(DataUpdateCoordinator[SolemStatus]):
    """Poll status and send manual commands to the SOLEM controller."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.station_count = entry.data[CONF_STATION_COUNT]
        self.default_duration = int(
            entry.options.get(CONF_DEFAULT_DURATION, entry.data.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION))
        )
        timeout = int(
            entry.options.get(
                CONF_BLUETOOTH_TIMEOUT,
                entry.data.get(CONF_BLUETOOTH_TIMEOUT, DEFAULT_BLUETOOTH_TIMEOUT),
            )
        )
        self.client = SolemBleClient(self.address, timeout=timeout)
        self.active_station: int | None = None
        poll_interval = int(
            entry.options.get(CONF_POLL_INTERVAL, entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=timedelta(seconds=poll_interval),
        )

    def _async_set_latest_ble_device(self) -> bool:
        """Refresh the client target from Home Assistant's Bluetooth manager."""
        from homeassistant.components.bluetooth import async_ble_device_from_address

        ble_device = async_ble_device_from_address(self.hass, self.address, connectable=True)
        self.client.set_ble_device(ble_device)
        return ble_device is not None

    async def _async_update_data(self) -> SolemStatus:
        try:
            if not self._async_set_latest_ble_device():
                raise UpdateFailed("SOLEM device is not currently available via Home Assistant Bluetooth")
            status = await self.client.read_status()
            if status.mode is not SolemMode.SINGLE_STATION_ACTIVE:
                self.active_station = None
            return status
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Unable to read SOLEM status: {err}") from err

    async def async_start_station(self, station: int) -> None:
        """Start one station for the configured duration."""
        if not self._async_set_latest_ble_device():
            raise HomeAssistantError("SOLEM device is not currently available via Home Assistant Bluetooth")
        self.active_station = station
        await self.client.send_command(build_station_command(station, self.default_duration))
        await self.async_request_refresh()

    async def async_start_all(self) -> None:
        """Start all stations for the configured duration."""
        if not self._async_set_latest_ble_device():
            raise HomeAssistantError("SOLEM device is not currently available via Home Assistant Bluetooth")
        self.active_station = None
        await self.client.send_command(build_all_stations_command(self.default_duration))
        await self.async_request_refresh()

    async def async_stop(self) -> None:
        """Stop manual watering."""
        if not self._async_set_latest_ble_device():
            raise HomeAssistantError("SOLEM device is not currently available via Home Assistant Bluetooth")
        self.active_station = None
        await self.client.send_command(stop_command())
        await self.async_request_refresh()
