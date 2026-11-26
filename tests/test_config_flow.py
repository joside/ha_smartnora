"""Tests for the Smart NORA config flow."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smartnora.api import NoraAuthError
from custom_components.smartnora.const import DOMAIN

USER_INPUT = {"email": "test@example.com", "password": "secret", "group": "home.assistant"}


async def _ok_login(self) -> None:
    self.uid = "uid123"


async def _bad_login(self) -> None:
    raise NoraAuthError("INVALID_LOGIN_CREDENTIALS")


async def test_user_flow_success(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with (
        patch("custom_components.smartnora.config_flow.NoraAuth.async_login", _ok_login),
        patch("custom_components.smartnora.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["group"] == "home:assistant"
    assert result["result"].unique_id == "uid123:home:assistant"


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.smartnora.config_flow.NoraAuth.async_login", _bad_login):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
