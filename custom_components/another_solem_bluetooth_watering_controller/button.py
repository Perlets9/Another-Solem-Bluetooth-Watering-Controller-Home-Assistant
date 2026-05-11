"""Button entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            StopButton(coordinator),
            ResetBluetoothConnectionButton(coordinator),
        ]
    )


class StopButton(SolemEntity, ButtonEntity):
    """Stop manual watering."""

    _attr_name = "Stop"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "stop")

    async def async_press(self) -> None:
        await self.coordinator.async_stop()


class ResetBluetoothConnectionButton(SolemEntity, ButtonEntity):
    """Reset the local BLE connection for diagnostics."""

    _attr_name = "Reset Bluetooth Connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "reset-bluetooth-connection")

    async def async_press(self) -> None:
        await self.coordinator.async_reset_connection()
