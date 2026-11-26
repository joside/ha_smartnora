"""Config flow for Smart NORA."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .api import NoraAuth, NoraAuthError, NoraError, escape_firebase_path
from .const import CONF_ENTITIES, CONF_GROUP, DEFAULT_GROUP, DOMAIN, SUPPORTED_DOMAINS


class SmartNoraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            group = escape_firebase_path(user_input[CONF_GROUP].strip()) or DEFAULT_GROUP
            auth = NoraAuth(
                async_get_clientsession(self.hass),
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
            )
            try:
                await auth.async_login()
            except NoraAuthError:
                errors["base"] = "invalid_auth"
            except (NoraError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{auth.uid}:{group}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{user_input[CONF_EMAIL]} ({group})",
                    data={**user_input, CONF_GROUP: group},
                    options={CONF_ENTITIES: []},
                )

        schema = vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_GROUP, default=DEFAULT_GROUP): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials stopped working (e.g. password changed)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            auth = NoraAuth(
                async_get_clientsession(self.hass),
                entry.data[CONF_EMAIL],
                user_input[CONF_PASSWORD],
            )
            try:
                await auth.async_login()
            except NoraAuthError:
                errors["base"] = "invalid_auth"
            except (NoraError, aiohttp.ClientError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return SmartNoraOptionsFlow()


class SmartNoraOptionsFlow(OptionsFlow):
    """Choose which entities are exposed to Google Home."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema({
            vol.Optional(
                CONF_ENTITIES,
                default=self.config_entry.options.get(CONF_ENTITIES, []),
            ): EntitySelector(EntitySelectorConfig(domain=list(SUPPORTED_DOMAINS), multiple=True)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
