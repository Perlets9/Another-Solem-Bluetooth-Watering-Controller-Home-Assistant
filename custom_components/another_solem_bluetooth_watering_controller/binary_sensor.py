"""Binary sensor entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .entity import SolemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    async_add_entities([IrrigatingBinarySensor(entry.runtime_data.coordinator)])


class IrrigatingBinarySensor(SolemEntity, BinarySensorEntity):
    """Whether the controller reports active irrigation."""

    _attr_name = "Irrigating"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "irrigating")

    @property
    def is_on(self) -> bool | None:
        return None if self.coordinator.data is None else self.coordinator.data.active
