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

from datetime import time

from .const import DOMAIN, MAX_DURATION, MIN_DURATION
from .programs import FrequencyType
from .protocol import MAX_PROGRAM, MIN_PROGRAM

if TYPE_CHECKING:
    from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_START_STATION = "start_station"
SERVICE_START_ALL_STATIONS = "start_all_stations"
SERVICE_STOP = "stop"
SERVICE_RUN_PROGRAM = "run_program"
SERVICE_CONFIGURE_PROGRAM = "configure_program"

ATTR_STATION = "station"
ATTR_DURATION = "duration"
ATTR_PROGRAM = "program"
ATTR_NAME = "name"
ATTR_WATER_BUDGET = "water_budget"
ATTR_FREQUENCY = "frequency"
ATTR_PERIOD_DAYS = "period_days"
ATTR_DAYS_OF_WEEK = "days_of_week"
ATTR_START_TIMES = "start_times"
ATTR_STATIONS = "stations"

# Same Monday-first bit ordering decoded from the snoop captures.
_DAY_BITS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

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

_PROGRAM_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=MIN_PROGRAM, max=MAX_PROGRAM))

_RUN_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): _DEVICE_IDS_SCHEMA,
        vol.Required(ATTR_PROGRAM): _PROGRAM_SCHEMA,
    }
)


def _parse_time_string(value: str) -> time:
    """Accept "HH:MM" strings from the service call."""
    parts = value.split(":")
    if len(parts) != 2:
        raise vol.Invalid(f"Invalid time '{value}', expected HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as err:
        raise vol.Invalid(f"Invalid time '{value}', expected HH:MM") from err
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise vol.Invalid(f"Time out of range '{value}'")
    return time(hour=hour, minute=minute)


_CONFIGURE_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): _DEVICE_IDS_SCHEMA,
        vol.Required(ATTR_PROGRAM): _PROGRAM_SCHEMA,
        vol.Optional(ATTR_NAME): vol.All(str, vol.Length(max=16)),
        vol.Optional(ATTR_WATER_BUDGET): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=200)
        ),
        vol.Optional(ATTR_FREQUENCY): vol.In([t.value for t in FrequencyType]),
        vol.Optional(ATTR_PERIOD_DAYS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
        vol.Optional(ATTR_DAYS_OF_WEEK): vol.All(
            [vol.In(list(_DAY_BITS))], vol.Length(min=0, max=7)
        ),
        vol.Optional(ATTR_START_TIMES): vol.All(
            [_parse_time_string], vol.Length(min=0, max=8)
        ),
        vol.Optional(ATTR_STATIONS): {
            vol.All(vol.Coerce(int), vol.Range(min=1, max=6)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=MAX_DURATION)
            )
        },
    }
)


def _build_program_changes(call_data: dict) -> dict:
    """Translate raw service-call values into kwargs for ``apply_program_changes``."""
    changes: dict = {}
    if ATTR_NAME in call_data:
        changes["name"] = call_data[ATTR_NAME]
    if ATTR_WATER_BUDGET in call_data:
        changes["water_budget"] = call_data[ATTR_WATER_BUDGET]
    if ATTR_FREQUENCY in call_data:
        changes["frequency_type"] = FrequencyType(call_data[ATTR_FREQUENCY])
    if ATTR_PERIOD_DAYS in call_data:
        changes["period_days"] = call_data[ATTR_PERIOD_DAYS]
    if ATTR_DAYS_OF_WEEK in call_data:
        bitmap = 0
        for day in call_data[ATTR_DAYS_OF_WEEK]:
            bitmap |= 1 << _DAY_BITS[day]
        changes["dow_bitmap"] = bitmap
    if ATTR_START_TIMES in call_data:
        changes["start_times"] = list(call_data[ATTR_START_TIMES])
    if ATTR_STATIONS in call_data:
        # Service exposes durations in minutes (matches the rest of the
        # integration); the firmware stores them in seconds, hence the *60.
        changes["stations"] = {
            int(idx): int(minutes) * 60
            for idx, minutes in call_data[ATTR_STATIONS].items()
        }
    return changes


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

    async def _handle_run_program(call: ServiceCall) -> None:
        program = call.data[ATTR_PROGRAM]
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_run_program(program)

    async def _handle_configure_program(call: ServiceCall) -> None:
        program = call.data[ATTR_PROGRAM]
        changes = _build_program_changes(call.data)
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_configure_program(program, **changes)

    hass.services.async_register(
        DOMAIN, SERVICE_START_STATION, _handle_start_station, schema=_START_STATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_ALL_STATIONS, _handle_start_all, schema=_START_ALL_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP, _handle_stop, schema=_STOP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_PROGRAM, _handle_run_program, schema=_RUN_PROGRAM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_PROGRAM,
        _handle_configure_program,
        schema=_CONFIGURE_PROGRAM_SCHEMA,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove integration services. Called when the last entry is unloaded."""
    for service in (
        SERVICE_START_STATION,
        SERVICE_START_ALL_STATIONS,
        SERVICE_STOP,
        SERVICE_RUN_PROGRAM,
        SERVICE_CONFIGURE_PROGRAM,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
