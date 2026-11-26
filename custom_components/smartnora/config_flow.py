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
from .sso_helper import HELPER_URL, async_register_sso_helper
from .const import (
    CONF_ENTITIES,
    CONF_GROUP,
    CONF_SSO,
    DEFAULT_GROUP,
    DOMAIN,
    SUPPORTED_DOMAINS,
)


class SmartNoraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def _async_try_login(self, data: dict[str, Any]) -> tuple[NoraAuth | None, str | None]:
        auth = NoraAuth(
            async_get_clientsession(self.hass),
            email=data.get(CONF_EMAIL),
            password=data.get(CONF_PASSWORD),
            sso=data.get(CONF_SSO),
        )
        try:
            await auth.async_login()
        except NoraAuthError:
            return None, "invalid_auth"
        except (NoraError, aiohttp.ClientError, KeyError, ValueError):
            return None, "cannot_connect"
        return auth, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the login method."""
        return self.async_show_menu(step_id="user", menu_options=["sso", "password"])

    async def _async_create(self, auth: NoraAuth, data: dict[str, Any]) -> ConfigFlowResult:
        group = escape_firebase_path(data[CONF_GROUP].strip()) or DEFAULT_GROUP
        await self.async_set_unique_id(f"{auth.uid}:{group}")
        self._abort_if_unique_id_configured()
        email = auth.email or "Smart NORA"
        return self.async_create_entry(
            title=f"{email} ({group})",
            data={**data, CONF_GROUP: group, CONF_EMAIL: email},
            options={CONF_ENTITIES: []},
        )

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Login with email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            auth, error = await self._async_try_login(user_input)
            if auth:
                return await self._async_create(auth, user_input)
            errors["base"] = error
        schema = vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_GROUP, default=DEFAULT_GROUP): str,
        })
        return self.async_show_form(step_id="password", data_schema=schema, errors=errors)

    async def async_step_sso(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Login with a Smart NORA SSO token (Google sign-in accounts)."""
        errors: dict[str, str] = {}
        async_register_sso_helper(self.hass)
        if user_input is not None:
            user_input[CONF_SSO] = user_input[CONF_SSO].strip()
            auth, error = await self._async_try_login(user_input)
            if auth:
                return await self._async_create(auth, user_input)
            errors["base"] = error
        schema = vol.Schema({
            vol.Required(CONF_SSO): str,
            vol.Required(CONF_GROUP, default=DEFAULT_GROUP): str,
        })
        return self.async_show_form(
            step_id="sso",
            data_schema=schema,
            errors=errors,
            description_placeholders={"helper_url": HELPER_URL},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials stopped working (password changed, SSO token revoked)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        async_register_sso_helper(self.hass)
        key = CONF_SSO if entry.data.get(CONF_SSO) else CONF_PASSWORD
        if user_input is not None:
            auth, error = await self._async_try_login(
                {CONF_EMAIL: entry.data.get(CONF_EMAIL), key: user_input[key].strip()}
            )
            if auth:
                return self.async_update_reload_and_abort(
                    entry, data_updates={key: user_input[key].strip()}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(key): str}),
            description_placeholders={"email": entry.data.get(CONF_EMAIL, ""), "helper_url": HELPER_URL},
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
