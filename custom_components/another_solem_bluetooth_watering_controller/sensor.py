"""Sensor entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .entity import SolemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            StatusSensor(coordinator),
            TimeRemainingSensor(coordinator),
            RawStatusSensor(coordinator),
        ]
    )


class StatusSensor(SolemEntity, SensorEntity):
    """Controller mode sensor."""

    _attr_name = "Status"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        return None if self.coordinator.data is None else self.coordinator.data.mode.value


class TimeRemainingSensor(SolemEntity, SensorEntity):
    """Remaining watering time."""

    _attr_name = "Time Remaining"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "time-remaining")

    @property
    def native_value(self) -> int | None:
        return None if self.coordinator.data is None else self.coordinator.data.timer_remaining


class RawStatusSensor(SolemEntity, SensorEntity):
    """Raw status notification for diagnostics."""

    _attr_name = "Raw Status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "raw-status")

    @property
    def native_value(self) -> str | None:
        return None if self.coordinator.data is None else self.coordinator.data.raw
