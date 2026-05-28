"""Button entities for SOLEM BL-IP."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SolemConfigEntry
from .entity import SolemEntity
from .protocol import MAX_PROGRAM, MIN_PROGRAM

_PROGRAM_DEFAULT_NAMES = {1: "Program A", 2: "Program B", 3: "Program C"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolemConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [
        StopButton(coordinator),
        RefreshStatusButton(coordinator),
        RefreshProgramsButton(coordinator),
        ResetBluetoothConnectionButton(coordinator),
    ]
    entities.extend(
        RunProgramButton(coordinator, program)
        for program in range(MIN_PROGRAM, MAX_PROGRAM + 1)
    )
    async_add_entities(entities)


class StopButton(SolemEntity, ButtonEntity):
    """Stop manual watering."""

    _attr_name = "Stop"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "stop")

    async def async_press(self) -> None:
        await self.coordinator.async_stop()


class RefreshStatusButton(SolemEntity, ButtonEntity):
    """Refresh the controller status on demand."""

    _attr_name = "Refresh Status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh-status")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_status()


class RefreshProgramsButton(SolemEntity, ButtonEntity):
    """Force an on-demand re-read of the 12 program slots."""

    _attr_name = "Refresh Programs"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh-programs")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_programs()


class ResetBluetoothConnectionButton(SolemEntity, ButtonEntity):
    """Reset the local BLE connection for diagnostics."""

    _attr_name = "Reset Bluetooth Connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "reset-bluetooth-connection")

    async def async_press(self) -> None:
        await self.coordinator.async_reset_connection()


class RunProgramButton(SolemEntity, ButtonEntity):
    """Run one of the controller's pre-configured programs on demand.

    The button's display name follows the program's name as read from the
    device (e.g. "Run Morning") when available. Until the program records
    are loaded we fall back to the static MySolem labels (Program A/B/C).
    """

    def __init__(self, coordinator, program: int) -> None:
        super().__init__(coordinator, f"run-program-{program}")
        self.program = program
        self._attr_translation_key = "run_program"

    @property
    def name(self) -> str:
        programs = getattr(self.coordinator, "programs", None)
        if programs and 0 <= self.program - 1 < len(programs):
            label = programs[self.program - 1].name
            if label:
                return f"Run {label}"
        return f"Run {_PROGRAM_DEFAULT_NAMES.get(self.program, f'Program {self.program}')}"

    async def async_press(self) -> None:
        await self.coordinator.async_run_program(self.program)
