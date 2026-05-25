"""Service registration for the SOLEM BL-IP integration.

The integration exposes three device-targeted services so that automations
can drive each station with its own duration without racing against the
global ``Watering Duration`` number entity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MAX_DURATION, MIN_DURATION

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_START_STATION = "start_station"
SERVICE_START_ALL_STATIONS = "start_all_stations"
SERVICE_STOP = "stop"

ATTR_STATION = "station"
ATTR_DURATION = "duration"

# Stations are validated against the protocol's hardcoded 1..6 range, not
# against ``coordinator.station_count``: that check is per-coordinator and
# done at command-build time. The schema-level bounds catch typos early.
_STATION_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=1, max=6))
_DURATION_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=MIN_DURATION, max=MAX_DURATION))
_DEVICE_IDS_SCHEMA = vol.All(
    lambda value: [value] if isinstance(value, str) else value,
    [str],
)

_START_STATION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): _DEVICE_IDS_SCHEMA,
        vol.Required(ATTR_STATION): _STATION_SCHEMA,
        vol.Required(ATTR_DURATION): _DURATION_SCHEMA,
    }
)

_START_ALL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): _DEVICE_IDS_SCHEMA,
        vol.Required(ATTR_DURATION): _DURATION_SCHEMA,
    }
)

_STOP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): _DEVICE_IDS_SCHEMA,
    }
)


def _coordinators_for_devices(
    hass: HomeAssistant, device_ids: list[str]
) -> list["SolemCoordinator"]:
    """Resolve target device IDs to SOLEM coordinators.

    A single service call can target multiple devices (HA's device target
    selector returns a list); we run the command on every matching SOLEM
    coordinator. Devices that don't belong to this integration are silently
    ignored, mirroring how built-in services handle mixed targets.
    """
    device_reg = dr.async_get(hass)
    domain_data: dict = hass.data.get(DOMAIN, {})
    coordinators: list["SolemCoordinator"] = []
    seen: set[str] = set()

    for device_id in device_ids:
        device = device_reg.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Unknown device: {device_id}")
        for entry_id in device.config_entries:
            if entry_id in seen:
                continue
            coordinator = domain_data.get(entry_id)
            if coordinator is not None:
                coordinators.append(coordinator)
                seen.add(entry_id)

    if not coordinators:
        raise HomeAssistantError(
            "No SOLEM BL-IP integration entries found for the targeted devices"
        )
    return coordinators


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration services. Safe to call multiple times."""
    if hass.services.has_service(DOMAIN, SERVICE_START_STATION):
        return

    async def _handle_start_station(call: ServiceCall) -> None:
        station = call.data[ATTR_STATION]
        duration = call.data[ATTR_DURATION]
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_start_station(station, duration)

    async def _handle_start_all(call: ServiceCall) -> None:
        duration = call.data[ATTR_DURATION]
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_start_all(duration)

    async def _handle_stop(call: ServiceCall) -> None:
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_stop()

    hass.services.async_register(
        DOMAIN, SERVICE_START_STATION, _handle_start_station, schema=_START_STATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_ALL_STATIONS, _handle_start_all, schema=_START_ALL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP, _handle_stop, schema=_STOP_SCHEMA
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove integration services. Called when the last entry is unloaded."""
    for service in (SERVICE_START_STATION, SERVICE_START_ALL_STATIONS, SERVICE_STOP):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
