"""Sensor entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
            BatterySensor(coordinator),
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


class BatterySensor(SolemEntity, SensorEntity):
    """Experimental battery level sensor.

    Reads the 11th byte of the controller's status packet, which empirical
    evidence (cross-session captures) strongly suggests is a battery
    percentage. Disabled by default until verified over a longer period.
    """

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "battery")

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.battery_level


class RawStatusSensor(SolemEntity, SensorEntity):
    """Raw status notification for diagnostics."""

    _attr_name = "Raw Status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "raw-status")

    @property
    def native_value(self) -> str | None:
        return None if self.coordinator.data is None else self.coordinator.data.raw
