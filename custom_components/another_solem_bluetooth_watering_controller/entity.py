"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import SolemCoordinator


class SolemEntity(CoordinatorEntity[SolemCoordinator]):
    """Base entity for the SOLEM controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolemCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.entry.data.get(CONF_NAME, "SOLEM BL-IP"),
            manufacturer="SOLEM",
            model="BL-IP",
        )
