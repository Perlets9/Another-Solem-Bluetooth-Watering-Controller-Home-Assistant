"""Number entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .const import MAX_DURATION, MIN_DURATION
from .entity import SolemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities."""
    async_add_entities([DefaultDurationNumber(entry.runtime_data.coordinator)])


class DefaultDurationNumber(SolemEntity, NumberEntity):
    """Duration used by the station switches when turned on.

    The class name preserves the original ``default-duration`` unique_id so
    existing installations keep their entity registry mapping. The user-
    facing name is just ``Watering Duration`` because there is no
    non-default override path: this is *the* duration used for every manual
    start triggered from Home Assistant.
    """

    _attr_name = "Watering Duration"
    _attr_native_min_value = MIN_DURATION
    _attr_native_max_value = MAX_DURATION
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default-duration")

    @property
    def native_value(self) -> int:
        return self.coordinator.default_duration

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.default_duration = int(value)
        self.async_write_ha_state()
