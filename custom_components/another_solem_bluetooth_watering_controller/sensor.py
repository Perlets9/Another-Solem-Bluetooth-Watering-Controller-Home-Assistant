"""Sensor entities for SOLEM BL-IP."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SolemConfigEntry
from .entity import SolemEntity
from .programs import Program
from .protocol import MAX_PROGRAM, MIN_PROGRAM


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        StatusSensor(coordinator),
        TimeRemainingSensor(coordinator),
        BatterySensor(coordinator),
        RssiSensor(coordinator),
        LastWateringTimeSensor(coordinator),
        LastWateringStationSensor(coordinator),
        LastWateringDurationSensor(coordinator),
        RawStatusSensor(coordinator),
    ]
    for program in range(MIN_PROGRAM, MAX_PROGRAM + 1):
        entities.extend(
            [
                ProgramNameSensor(coordinator, program),
                ProgramFrequencySensor(coordinator, program),
                ProgramWaterBudgetSensor(coordinator, program),
                ProgramStartTimesSensor(coordinator, program),
                ProgramStationsSensor(coordinator, program),
            ]
        )
    async_add_entities(entities)


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


class RssiSensor(SolemEntity, SensorEntity):
    """Bluetooth signal strength as seen by the local adapter or proxy.

    Refreshed from the coordinator's passive Bluetooth listener (no BLE
    round-trip required), so it works even when the controller is asleep
    but still advertising.
    """

    _attr_name = "Signal Strength"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "rssi")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.rssi

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_bluetooth_state_listener(
                self._handle_bluetooth_update
            )
        )

    @callback
    def _handle_bluetooth_update(self) -> None:
        self.async_write_ha_state()


class _RestoreLastWateringMixin(SolemEntity, RestoreSensor):
    """Common boilerplate for the last-watering sensor family.

    Restores the previously published value so HA reboots don't blank out
    the "last cycle" info, and re-publishes whenever the coordinator
    detects a fresh watering cycle.
    """

    _coord_attribute: str = ""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None and self._coord_attribute:
            setattr(
                self.coordinator,
                self._coord_attribute,
                self._normalize_restored(last.native_value),
            )

    def _normalize_restored(self, value):
        """Hook for subclasses to coerce restored types (datetime/int)."""
        return value


class LastWateringTimeSensor(_RestoreLastWateringMixin):
    """Timestamp at which the last completed watering cycle ended."""

    _attr_name = "Last Watering"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _coord_attribute = "last_watering_time"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last-watering-time")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_watering_time

    def _normalize_restored(self, value):
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return dt_util.parse_datetime(value)
        return None


class LastWateringStationSensor(_RestoreLastWateringMixin):
    """Station number that ran in the last completed watering cycle."""

    _attr_name = "Last Watering Station"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _coord_attribute = "last_watering_station"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last-watering-station")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.last_watering_station

    def _normalize_restored(self, value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class LastWateringDurationSensor(_RestoreLastWateringMixin):
    """Duration (seconds) of the last completed watering cycle."""

    _attr_name = "Last Watering Duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _coord_attribute = "last_watering_duration"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last-watering-duration")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.last_watering_duration

    def _normalize_restored(self, value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


_PROGRAM_DEFAULT_LABELS = {1: "Program A", 2: "Program B", 3: "Program C"}


class _ProgramSensorBase(SolemEntity, SensorEntity):
    """Common boilerplate for sensors that surface a single program's field.

    Subscribes to the coordinator's program-refresh listener so the
    entities push updates immediately after a fresh dump instead of
    relying on the next polling cycle (the dump runs out-of-band).
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _key_suffix: str = ""
    _name_suffix: str = ""

    def __init__(self, coordinator, program: int) -> None:
        super().__init__(coordinator, f"program-{program}-{self._key_suffix}")
        self.program = program

    @property
    def _label(self) -> str:
        return _PROGRAM_DEFAULT_LABELS.get(self.program, f"Program {self.program}")

    @property
    def name(self) -> str:
        return f"{self._label} {self._name_suffix}"

    @property
    def _program(self) -> Program | None:
        programs = getattr(self.coordinator, "programs", None)
        if not programs:
            return None
        idx = self.program - 1
        if 0 <= idx < len(programs):
            return programs[idx]
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_programs_listener(self._handle_programs_update)
        )

    @callback
    def _handle_programs_update(self) -> None:
        self.async_write_ha_state()


class ProgramNameSensor(_ProgramSensorBase):
    _key_suffix = "name"
    _name_suffix = "Name"

    @property
    def native_value(self) -> str | None:
        program = self._program
        return program.name if program else None


class ProgramFrequencySensor(_ProgramSensorBase):
    _key_suffix = "frequency"
    _name_suffix = "Frequency"

    @property
    def native_value(self) -> str | None:
        program = self._program
        return program.frequency_label if program else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        program = self._program
        if program is None:
            return None
        return {
            "frequency_type": program.frequency_type.value,
            "period_days": program.period_days,
            "days_to_next": program.days_to_next,
            "dow_bitmap": program.dow_bitmap,
        }


class ProgramWaterBudgetSensor(_ProgramSensorBase):
    _key_suffix = "water-budget"
    _name_suffix = "Water Budget"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        program = self._program
        return program.water_budget if program else None


class ProgramStartTimesSensor(_ProgramSensorBase):
    _key_suffix = "start-times"
    _name_suffix = "Start Times"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        program = self._program
        return len(program.start_times) if program else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        program = self._program
        if program is None:
            return None
        return {
            "start_times": [t.strftime("%H:%M") for t in program.start_times],
        }


class ProgramStationsSensor(_ProgramSensorBase):
    _key_suffix = "stations"
    _name_suffix = "Stations"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        program = self._program
        return len(program.stations) if program else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        program = self._program
        if program is None:
            return None
        return {
            "stations": {str(idx): seconds for idx, seconds in program.stations.items()},
        }


class RawStatusSensor(SolemEntity, SensorEntity):
    """Raw status notification for diagnostics."""

    _attr_name = "Raw Status"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "raw-status")

    @property
    def native_value(self) -> str | None:
        return None if self.coordinator.data is None else self.coordinator.data.raw
