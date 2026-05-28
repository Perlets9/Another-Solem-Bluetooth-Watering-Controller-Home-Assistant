"""Switch entities for SOLEM BL-IP stations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SolemConfigEntry
from .entity import SolemEntity
from .protocol import MAX_PROGRAM, MIN_PROGRAM, SolemMode

_PROGRAM_DEFAULT_NAMES = {1: "Program A", 2: "Program B", 3: "Program C"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = [
        StationSwitch(coordinator, station)
        for station in range(1, coordinator.station_count + 1)
    ]
    entities.append(AllStationsSwitch(coordinator))
    entities.extend(
        RunProgramSwitch(coordinator, program)
        for program in range(MIN_PROGRAM, MAX_PROGRAM + 1)
    )
    async_add_entities(entities)


class StationSwitch(SolemEntity, SwitchEntity, RestoreEntity):
    """Switch that starts a station for the configured duration.

    Restores its last on/off state across HA restarts so it doesn't flash to
    ``unknown`` while the first BLE poll is in flight. If the restored state
    is ``on`` we also seed ``coordinator.active_station`` so the post-restart
    UI keeps reflecting which station is running until a fresh status read.
    """

    def __init__(self, coordinator, station: int) -> None:
        super().__init__(coordinator, f"station-{station}")
        self.station = station
        self._attr_translation_key = "station"
        self._attr_name = f"Station {station}"
        self._restored_is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._restored_is_on = last.state == STATE_ON
            if self._restored_is_on and self.coordinator.active_station is None:
                self.coordinator.active_station = self.station

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return self._restored_is_on
        return (
            self.coordinator.data.mode is SolemMode.SINGLE_STATION_ACTIVE
            and self.coordinator.active_station == self.station
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_station(self.station)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()


class AllStationsSwitch(SolemEntity, SwitchEntity, RestoreEntity):
    """Switch that starts all stations for the configured duration."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "all-stations")
        self._attr_name = "All Stations"
        self._restored_is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._restored_is_on = last.state == STATE_ON

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return self._restored_is_on
        return self.coordinator.data.mode is SolemMode.ALL_STATIONS_ACTIVE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_all()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()


class RunProgramSwitch(SolemEntity, SwitchEntity, RestoreEntity):
    """Switch reflecting whether the matching configured program is running.

    The controller's status doesn't tell us *which* program is executing,
    only that a station is active; we rely on the locally tracked
    ``coordinator.active_program`` to drive ``is_on``.
    """

    def __init__(self, coordinator, program: int) -> None:
        super().__init__(coordinator, f"run-program-{program}-switch")
        self.program = program
        self._attr_translation_key = "run_program"
        self._attr_entity_registry_enabled_default = False
        self._restored_is_on: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._restored_is_on = last.state == STATE_ON
            if self._restored_is_on and self.coordinator.active_program is None:
                self.coordinator.active_program = self.program

    @property
    def name(self) -> str:
        programs = getattr(self.coordinator, "programs", None)
        if programs and 0 <= self.program - 1 < len(programs):
            label = programs[self.program - 1].name
            if label:
                return label
        return _PROGRAM_DEFAULT_NAMES.get(self.program, f"Program {self.program}")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return self._restored_is_on
        if not self.coordinator.data.active:
            return False
        return getattr(self.coordinator, "active_program", None) == self.program

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_run_program(self.program)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()
