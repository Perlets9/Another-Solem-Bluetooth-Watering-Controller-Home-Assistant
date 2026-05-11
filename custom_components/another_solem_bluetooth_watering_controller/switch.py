"""Switch entities for SOLEM BL-IP stations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .entity import SolemEntity
from .protocol import SolemMode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data.coordinator
    entities = [StationSwitch(coordinator, station) for station in range(1, coordinator.station_count + 1)]
    entities.append(AllStationsSwitch(coordinator))
    async_add_entities(entities)


class StationSwitch(SolemEntity, SwitchEntity):
    """Switch that starts a station for the configured duration."""

    def __init__(self, coordinator, station: int) -> None:
        super().__init__(coordinator, f"station-{station}")
        self.station = station
        self._attr_translation_key = "station"
        self._attr_name = f"Station {station}"

    @property
    def is_on(self) -> bool:
        return (
            self.coordinator.data is not None
            and self.coordinator.data.mode is SolemMode.SINGLE_STATION_ACTIVE
            and self.coordinator.active_station == self.station
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_station(self.station)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()


class AllStationsSwitch(SolemEntity, SwitchEntity):
    """Switch that starts all stations for the configured duration."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "all-stations")
        self._attr_name = "All Stations"

    @property
    def is_on(self) -> bool:
        return self.coordinator.data is not None and self.coordinator.data.mode is SolemMode.ALL_STATIONS_ACTIVE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_start_all()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()
