"""Another Solem Bluetooth Watering Controller integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SolemCoordinator
from .const import DOMAIN

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class RuntimeData:
    """Runtime data stored on the config entry."""

    coordinator: SolemCoordinator


type SolemConfigEntry = ConfigEntry[RuntimeData]


async def _async_reload_entry(hass: HomeAssistant, entry: SolemConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: SolemConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    coordinator = SolemCoordinator(hass, entry)
    entry.runtime_data = RuntimeData(coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolemConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
