"""Binary sensor entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SolemConfigEntry
from .entity import SolemEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    async_add_entities([IrrigatingBinarySensor(entry.runtime_data.coordinator)])


class IrrigatingBinarySensor(SolemEntity, BinarySensorEntity, RestoreEntity):
    """Whether the controller reports active irrigation.

    Restores its last known on/off across Home Assistant restarts so the
    entity does not flash to ``unknown`` while we wait for the first
    successful BLE poll (the BL-IP advertises infrequently).
    """

    _attr_name = "Irrigating"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "irrigating")
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
        return self.coordinator.data.active
