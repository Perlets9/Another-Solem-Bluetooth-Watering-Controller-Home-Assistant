"""Button entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .entity import SolemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    async_add_entities([StopButton(entry.runtime_data.coordinator)])


class StopButton(SolemEntity, ButtonEntity):
    """Stop manual watering."""

    _attr_name = "Stop"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "stop")

    async def async_press(self) -> None:
        await self.coordinator.async_stop()
