"""Tests for SOLEM BL-IP config and options flows."""

from types import SimpleNamespace

from custom_components.another_solem_bluetooth_watering_controller.config_flow import (
    SolemConfigFlow,
    SolemOptionsFlow,
)



def test_options_flow_can_be_created_by_home_assistant_without_assigning_config_entry() -> None:
    flow = SolemConfigFlow.async_get_options_flow(SimpleNamespace())

    assert isinstance(flow, SolemOptionsFlow)
