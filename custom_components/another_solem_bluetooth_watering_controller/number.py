"""Number entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .const import (
    CONF_ENABLE_PROGRAM_EDITING,
    DEFAULT_ENABLE_PROGRAM_EDITING,
    MAX_DURATION,
    MIN_DURATION,
)
from .entity import SolemEntity
from .protocol import MAX_PROGRAM, MIN_PROGRAM

_PROGRAM_LABELS = {1: "Program A", 2: "Program B", 3: "Program C"}


def _editing_enabled(entry: SolemConfigEntry) -> bool:
    """Whether the user enabled the program-editing entities for this entry."""
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
    """Set up number entities."""
    coordinator = entry.runtime_data.coordinator
    entities: list[NumberEntity] = [DefaultDurationNumber(coordinator)]
    if _editing_enabled(entry):
        entities.extend(
            ProgramWaterBudgetNumber(coordinator, program)
            for program in range(MIN_PROGRAM, MAX_PROGRAM + 1)
        )
    async_add_entities(entities)


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


class ProgramWaterBudgetNumber(SolemEntity, NumberEntity):
    """Editable water budget (0-200%) for a single program."""

    _attr_native_min_value = 0
    _attr_native_max_value = 200
    _attr_native_step = 10
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, program: int) -> None:
        super().__init__(coordinator, f"program-{program}-water-budget-control")
        self.program = program
        self._attr_name = f"{_PROGRAM_LABELS.get(program, f'Program {program}')} Water Budget"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_programs_listener(self._handle_programs_update)
        )

    @callback
    def _handle_programs_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        programs = getattr(self.coordinator, "programs", None) or []
        idx = self.program - 1
        if 0 <= idx < len(programs):
            return programs[idx].water_budget
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_configure_program(
            self.program, water_budget=int(value)
        )
