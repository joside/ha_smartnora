"""Runtime bridge between Home Assistant and Smart NORA.

Corresponds to FirebaseSync / FirebaseDevice / FirebaseSceneDevice in the
Node-RED plugin:

* SYNC of all exposed entities (POST /client/sync)
* Report State on HA state changes (POST /client/update-state)
* Heartbeat every 60 s (user/<uid>/version/<group>/heartbeat)
* Stream device_states/<uid>/<group> -> Google commands (update.by != client)
* Stream device_nora/<uid>/<group> -> pendingScene (scene activation), errors
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from typing import Any

import aiohttp
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .api import NoraClient, NoraError, apply_stream_event
from .const import HEARTBEAT_INTERVAL_SEC, STATE_DEBOUNCE_SEC, SYNC_DEBOUNCE_SEC
from .devices import ADAPTERS, Adapter, Command

_LOGGER = logging.getLogger(__name__)


def _hash(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()


class NoraBridge:
    """Keeps one Smart NORA group in sync with a set of HA entities."""

    def __init__(self, hass: HomeAssistant, client: NoraClient, entity_ids: list[str]) -> None:
        self.hass = hass
        self.client = client
        self.entity_ids = entity_ids
        self.adapters: dict[str, Adapter] = {}  # by NORA device id
        self._by_entity: dict[str, Adapter] = {}
        self._tasks: list[asyncio.Task] = []
        self._unsub = None
        self._last_sync_hash = ""
        self._sync_handle: asyncio.TimerHandle | None = None
        self._reported: dict[str, str] = {}
        self._report_handles: dict[str, asyncio.TimerHandle] = {}
        self._last_cmd_ts: dict[str, Any] = {}
        self._states_tree: dict[str, Any] = {}
        self._nora_tree: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self.connected = False

    # ---------------------------------------------------------- lifecycle --
    async def async_start(self) -> None:
        self._build_adapters()
        try:
            await self.client.async_update_offset()
        except NoraError as err:
            _LOGGER.warning("could not get server time offset: %s", err)
        await self._async_sync(force=True)
        for adapter in self.adapters.values():
            await self._async_report(adapter)

        self._unsub = async_track_state_change_event(
            self.hass, list(self._by_entity), self._handle_ha_state_change
        )
        uid, group = self.client.auth.uid, self.client.group
        self._tasks = [
            self.hass.async_create_background_task(self._heartbeat_loop(), "smartnora heartbeat"),
            self.hass.async_create_background_task(
                self._stream_loop(f"device_states/{uid}/{group}", self._on_states_event),
                "smartnora states stream",
            ),
            self.hass.async_create_background_task(
                self._stream_loop(f"device_nora/{uid}/{group}", self._on_nora_event),
                "smartnora nora stream",
            ),
        ]

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
        for handle in [self._sync_handle, *self._report_handles.values()]:
            if handle:
                handle.cancel()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ----------------------------------------------------------- adapters --
    def _build_adapters(self) -> None:
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        for entity_id in self.entity_ids:
            cls = ADAPTERS.get(entity_id.split(".")[0])
            if cls is None:
                continue
            state = self.hass.states.get(entity_id)
            name = state.name if state else entity_id
            area_id = None
            if entry := ent_reg.async_get(entity_id):
                area_id = entry.area_id
                if not area_id and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
                    area_id = dev.area_id
            area = area_reg.async_get_area(area_id) if area_id else None
            adapter = cls(self.hass, entity_id, name, area.name if area else None)
            self.adapters[adapter.device_id] = adapter
            self._by_entity[entity_id] = adapter

    # --------------------------------------------------------------- sync --
    def _definitions(self) -> list[dict[str, Any]]:
        return sorted((a.definition() for a in self.adapters.values()), key=lambda d: d["id"])

    async def _async_sync(self, force: bool = False) -> None:
        devices = self._definitions()
        sync_hash = _hash([{k: v for k, v in d.items() if k != "state"} for d in devices])
        if not force and sync_hash == self._last_sync_hash:
            return
        await self.client.async_sync(devices)
        self._last_sync_hash = sync_hash
        _LOGGER.info("synced %d device(s) to Smart NORA group %s", len(devices), self.client.group)

    @callback
    def _schedule_sync(self) -> None:
        if self._sync_handle:
            self._sync_handle.cancel()
        self._sync_handle = self.hass.loop.call_later(
            SYNC_DEBOUNCE_SEC,
            lambda: self.hass.async_create_task(self._safe(self._async_sync())),
        )

    # ------------------------------------------------------- report state --
    async def _async_report(self, adapter: Adapter) -> None:
        state = adapter.state()
        state_hash = _hash(state)
        if self._reported.get(adapter.device_id) == state_hash:
            return
        await self.client.async_update_state(adapter.device_id, state)
        self._reported[adapter.device_id] = state_hash

    @callback
    def _handle_ha_state_change(self, event: Event[EventStateChangedData]) -> None:
        adapter = self._by_entity.get(event.data["entity_id"])
        if adapter is None:
            return
        self._schedule_sync()  # e.g. light got dimmable after becoming available
        if handle := self._report_handles.get(adapter.device_id):
            handle.cancel()
        self._report_handles[adapter.device_id] = self.hass.loop.call_later(
            STATE_DEBOUNCE_SEC,
            lambda: self.hass.async_create_task(self._safe(self._async_report(adapter))),
        )

    # ------------------------------------------------------------ streams --
    async def _stream_loop(self, path: str, handler) -> None:
        while True:
            try:
                await self.client.async_stream(path, handler)
                self.connected = True
                continue  # token renewal; reconnect immediately
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                self.connected = False
                delay = random.uniform(30, 90)
                _LOGGER.warning("stream %s failed (%s), retry in %.0fs", path, err, delay)
                await asyncio.sleep(delay)

    async def _on_states_event(self, event: str, path: str, data: Any) -> None:
        self.connected = True
        first = not self._states_tree and path == "/"
        self._states_tree = apply_stream_event(self._states_tree, event, path, data)
        for device_id, entry in self._states_tree.items():
            adapter = self.adapters.get(device_id)
            if adapter is None or not isinstance(entry, dict):
                continue
            update = entry.get("update") or {}
            if update.get("by") == "client":
                continue
            ts = update.get("timestamp")
            if self._last_cmd_ts.get(device_id) == ts:
                continue
            self._last_cmd_ts[device_id] = ts
            if first:
                continue  # initial snapshot, not a new command
            new_state = entry.get("state") or {}
            for cmd in adapter.commands_for(adapter.state(), new_state):
                await self._call(cmd)

    async def _on_nora_event(self, event: str, path: str, data: Any) -> None:
        self._nora_tree = apply_stream_event(self._nora_tree, event, path, data)
        for device_id, entry in list(self._nora_tree.items()):
            adapter = self.adapters.get(device_id)
            if adapter is None or not isinstance(entry, dict):
                continue
            err = (entry.get("error") or {}).get("msg")
            if err and self._errors.get(device_id) != err:
                _LOGGER.warning("Smart NORA reports error for %s: %s", adapter.entity_id, err)
            self._errors[device_id] = err
            pending = entry.pop("pendingScene", None)
            if adapter.is_scene and pending:
                await self.client.async_db_delete(
                    f"device_nora/{self.client.auth.uid}/{self.client.group}/{device_id}/pendingScene"
                )
                if cmd := adapter.activate(bool(pending.get("deactivate"))):
                    await self._call(cmd)

    async def _call(self, cmd: Command) -> None:
        _LOGGER.debug("Google -> HA: %s.%s %s", cmd.domain, cmd.service, cmd.data)
        await self.hass.services.async_call(cmd.domain, cmd.service, cmd.data, blocking=False)

    # ---------------------------------------------------------- heartbeat --
    async def _heartbeat_loop(self) -> None:
        while True:
            await self._safe(self.client.async_heartbeat())
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def _safe(self, coro) -> None:
        try:
            await coro
        except (NoraError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Smart NORA request failed: %s", err)
