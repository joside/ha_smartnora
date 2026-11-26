"""Smart NORA integration: expose Home Assistant entities to Google Home."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NoraAuth, NoraAuthError, NoraClient, NoraError
from .bridge import NoraBridge
from .const import CONF_ENTITIES, CONF_GROUP, CONF_SSO, DEFAULT_GROUP

_LOGGER = logging.getLogger(__name__)

type SmartNoraConfigEntry = ConfigEntry[NoraBridge]


async def async_setup_entry(hass: HomeAssistant, entry: SmartNoraConfigEntry) -> bool:
    """Set up Smart NORA from a config entry."""
    session = async_get_clientsession(hass)
    auth = NoraAuth(
        session,
        email=entry.data.get(CONF_EMAIL),
        password=entry.data.get(CONF_PASSWORD),
        sso=entry.data.get(CONF_SSO),
    )
    try:
        await auth.async_login()
    except NoraAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (NoraError, aiohttp.ClientError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    client = NoraClient(session, auth, entry.data.get(CONF_GROUP, DEFAULT_GROUP))
    bridge = NoraBridge(hass, client, entry.options.get(CONF_ENTITIES, []))
    entry.runtime_data = bridge

    if hass.state is CoreState.running:
        try:
            await bridge.async_start()
        except (NoraError, aiohttp.ClientError) as err:
            await bridge.async_stop()
            raise ConfigEntryNotReady(str(err)) from err
    else:
        # wait until all integrations (e.g. WLED) are loaded, otherwise devices
        # would be synced without their capabilities
        fired = False

        async def _start_when_ready(_event) -> None:
            nonlocal fired
            fired = True
            try:
                await bridge.async_start()
            except (NoraError, aiohttp.ClientError) as err:
                _LOGGER.warning("Smart NORA start failed (%s), reloading", err)
                hass.config_entries.async_schedule_reload(entry.entry_id)

        unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_when_ready)
        entry.async_on_unload(lambda: None if fired else unsub())

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartNoraConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.async_stop()
    return True


async def _async_reload(hass: HomeAssistant, entry: SmartNoraConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
