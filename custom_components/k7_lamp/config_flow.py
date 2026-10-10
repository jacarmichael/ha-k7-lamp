"""Config flow: host + port, validated with a read-only ALL_READ."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_PORT, DOMAIN
from .protocol import K7Error, read_all


class K7ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                state = await read_all(user_input[CONF_HOST], user_input[CONF_PORT])
            except K7Error:
                errors["base"] = "cannot_connect"
            else:
                if "mini" in state.name.lower():
                    return self.async_abort(reason="unsupported_model")
                await self.async_set_unique_id(state.name or user_input[CONF_HOST])
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=state.name or user_input[CONF_HOST], data=user_input
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )
