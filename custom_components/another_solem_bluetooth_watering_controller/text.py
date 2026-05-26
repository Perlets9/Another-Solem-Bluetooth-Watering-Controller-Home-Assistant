"""Text entities for SOLEM BL-IP program editing.

Activated only when the user opts into program editing in the config flow
(``CONF_ENABLE_PROGRAM_EDITING``). Names are limited to 16 ASCII chars to
match the firmware's name row width.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .const import CONF_ENABLE_PROGRAM_EDITING, DEFAULT_ENABLE_PROGRAM_EDITING
from .entity import SolemEntity
from .protocol import MAX_PROGRAM, MIN_PROGRAM

_PROGRAM_LABELS = {1: "Program A", 2: "Program B", 3: "Program C"}


def _editing_enabled(entry: SolemConfigEntry) -> bool:
    return bool(
        entry.options.get(
            CONF_ENABLE_PROGRAM_EDITING,
            entry.data.get(CONF_ENABLE_PROGRAM_EDITING, DEFAULT_ENABLE_PROGRAM_EDITING),
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up text entities."""
    if not _editing_enabled(entry):
        return
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        ProgramNameText(coordinator, program)
        for program in range(MIN_PROGRAM, MAX_PROGRAM + 1)
    )


class ProgramNameText(SolemEntity, TextEntity):
    """Editable program name."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_max = 16
    _attr_pattern = r"^[\x20-\x7e]{0,16}$"

    def __init__(self, coordinator, program: int) -> None:
        super().__init__(coordinator, f"program-{program}-name-control")
        self.program = program
        self._attr_name = (
            f"{_PROGRAM_LABELS.get(program, f'Program {program}')} Name"
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_programs_listener(self._handle_programs_update)
        )

    @callback
    def _handle_programs_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str | None:
        programs = getattr(self.coordinator, "programs", None) or []
        idx = self.program - 1
        if 0 <= idx < len(programs):
            return programs[idx].name
        return None

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_configure_program(self.program, name=value)
