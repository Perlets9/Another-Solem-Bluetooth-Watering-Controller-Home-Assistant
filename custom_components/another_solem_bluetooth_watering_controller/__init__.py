"""Another Solem Bluetooth Watering Controller integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SolemCoordinator
from .const import DOMAIN
from .services import async_register_services, async_unregister_services

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

    # Start listening for the BL-IP's advertisements *before* the first
    # refresh so the cached BLEDevice is populated and the first connect
    # attempt has the best chance of succeeding even if HA's manager has
    # not seen the device recently.
    coordinator.async_start_bluetooth_listener()
    entry.async_on_unload(coordinator.async_stop_bluetooth_listener)

    entry.runtime_data = RuntimeData(coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    # Services are domain-wide (one set for all SOLEM devices) so we register
    # them once and never per-entry. Registration is idempotent.
    async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Best-effort initial refresh: avoids waiting for the (potentially long)
    # idle poll interval before any state is shown. ``async_refresh`` never
    # raises, so a failure here will simply leave entities as Unknown until
    # the next attempt.
    await coordinator.async_refresh()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolemConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            await coordinator.client.disconnect()
        # Drop the domain-wide services when no integration entries remain.
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
    return unload_ok
