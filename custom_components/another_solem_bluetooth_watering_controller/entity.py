"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAME, DOMAIN
from .coordinator import SolemCoordinator


class SolemEntity(CoordinatorEntity[SolemCoordinator]):
    """Base entity for the SOLEM controller."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolemCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}-{key}"
        info = coordinator.device_info
        model = info.model if info and info.model else "BL-IP"
        sw_version = info.firmware if info and info.firmware else None
        connections: set[tuple[str, str]] = set()
        if info and info.mac:
            connections.add((CONNECTION_BLUETOOTH, info.mac))
        else:
            connections.add((CONNECTION_BLUETOOTH, coordinator.address))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections=connections,
            name=coordinator.entry.data.get(CONF_NAME, "SOLEM BL-IP"),
            manufacturer="SOLEM",
            model=model,
            sw_version=sw_version,
        )
