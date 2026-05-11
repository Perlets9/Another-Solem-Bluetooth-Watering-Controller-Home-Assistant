"""Config flow for Another Solem Bluetooth Watering Controller."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .client import SolemBleClient
from .const import (
    CONF_ADDRESS,
    CONF_BLUETOOTH_TIMEOUT,
    CONF_DEFAULT_DURATION,
    CONF_NAME,
    CONF_POLL_INTERVAL,
    CONF_STATION_COUNT,
    DEFAULT_BLUETOOTH_TIMEOUT,
    DEFAULT_DURATION,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_DURATION,
    MIN_BLUETOOTH_TIMEOUT,
    MIN_DURATION,
    MIN_POLL_INTERVAL,
    SUPPORTED_STATION_COUNTS,
)


class SolemConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return SolemOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the user step."""
        errors: dict[str, str] = {}
        devices = await SolemBleClient.discover()
        device_options = [
            {"value": device.address, "label": f"{device.name or 'SOLEM BL-IP'} ({device.address})"}
            for device in devices
        ]

        if not device_options:
            errors["base"] = "no_devices"
            device_options = [{"value": "", "label": "No SOLEM BL-IP devices found"}]

        if user_input is not None and not errors:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            title = next(
                (option["label"] for option in device_options if option["value"] == address),
                f"SOLEM BL-IP {address}",
            )
            data = {
                CONF_ADDRESS: address,
                CONF_NAME: title,
                CONF_STATION_COUNT: int(user_input[CONF_STATION_COUNT]),
                CONF_DEFAULT_DURATION: user_input[CONF_DEFAULT_DURATION],
                CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                CONF_BLUETOOTH_TIMEOUT: user_input[CONF_BLUETOOTH_TIMEOUT],
            }
            return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): selector(
                    {"select": {"options": device_options, "mode": "dropdown"}}
                ),
                vol.Required(CONF_STATION_COUNT): selector(
                    {
                        "select": {
                            "options": [
                                {"value": str(count), "label": str(count)}
                                for count in SUPPORTED_STATION_COUNTS
                            ],
                            "mode": "dropdown",
                        }
                    }
                ),
                vol.Required(CONF_DEFAULT_DURATION, default=DEFAULT_DURATION): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_DURATION, max=MAX_DURATION)
                ),
                vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL)
                ),
                vol.Required(CONF_BLUETOOTH_TIMEOUT, default=DEFAULT_BLUETOOTH_TIMEOUT): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_BLUETOOTH_TIMEOUT)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class SolemOptionsFlow(OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEFAULT_DURATION,
                    default=current.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_DURATION, max=MAX_DURATION)),
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL)),
                vol.Required(
                    CONF_BLUETOOTH_TIMEOUT,
                    default=current.get(CONF_BLUETOOTH_TIMEOUT, DEFAULT_BLUETOOTH_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_BLUETOOTH_TIMEOUT)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
