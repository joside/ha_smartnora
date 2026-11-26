"""Low level client for the Smart NORA cloud.

Re-implements what node-red-contrib-smartnora does with the Firebase JS SDK,
using only the public REST interfaces:

* Firebase Auth REST API (email/password sign-in, token refresh)
* Firebase Realtime Database REST API (reads, writes, SSE streaming)
* Smart NORA HTTP API (api.smart-nora.eu/client/...)

See PROTOCOL.md in the repository root for details.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
import gzip
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    API_ENDPOINT,
    AUTH_CUSTOM_TOKEN_URL,
    AUTH_REFRESH_URL,
    AUTH_SIGNIN_URL,
    FIREBASE_API_KEY,
    FIREBASE_DB_URL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Refresh the ID token this many seconds before it expires (tokens live 1 h).
TOKEN_REFRESH_MARGIN = 300


class NoraError(Exception):
    """Generic Smart NORA error."""


class NoraAuthError(NoraError):
    """Credentials rejected."""


class NoraHttpError(NoraError):
    """Non-2xx answer from the Smart NORA API."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


def escape_firebase_path(value: str) -> str:
    """Same as escapeFirebasePath() in the Node-RED plugin."""
    for char in ".#$[]":
        value = value.replace(char, ":")
    return value


def _jwt_claims(token: str) -> dict[str, Any]:
    """Decode the (unverified) payload of a Firebase ID token."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


class NoraAuth:
    """Firebase authentication (email/password or Smart NORA SSO token)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str | None = None,
        password: str | None = None,
        sso: str | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._sso = sso
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self.uid: str | None = None
        self.email: str | None = email

    async def async_login(self) -> None:
        """Sign in (signInWithEmailAndPassword / signInWithCustomToken)."""
        if self._password:
            data = await self._async_identity_call(
                AUTH_SIGNIN_URL,
                {"email": self._email, "password": self._password, "returnSecureToken": True},
            )
        elif self._sso:
            custom_token = await self._async_exchange_sso()
            data = await self._async_identity_call(
                AUTH_CUSTOM_TOKEN_URL, {"token": custom_token, "returnSecureToken": True}
            )
        else:
            raise NoraAuthError("neither password nor SSO token configured")
        self._store(data["idToken"], data["refreshToken"], data["expiresIn"])
        claims = _jwt_claims(data["idToken"])
        self.uid = data.get("localId") or claims.get("user_id") or claims.get("sub")
        self.email = self.email or claims.get("email")

    async def _async_identity_call(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        async with self._session.post(url, params={"key": FIREBASE_API_KEY}, json=body) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                message = (data or {}).get("error", {}).get("message", "")
                if resp.status == 400:
                    raise NoraAuthError(message or "login rejected")
                raise NoraError(f"login failed: HTTP {resp.status} {message}")
        return data

    async def _async_exchange_sso(self) -> str:
        """POST /sso/exchange: Smart NORA SSO token -> Firebase custom token."""
        async with self._session.post(
            f"{API_ENDPOINT}/sso/exchange",
            data=gzip.compress(json.dumps({"token": self._sso}).encode()),
            headers={
                "user-agent": USER_AGENT,
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        ) as resp:
            if resp.status == 400:
                raise NoraAuthError(f"invalid SSO token: {await resp.text()}")
            if resp.status != 200:
                raise NoraHttpError(resp.status, await resp.text())
            data = await resp.json(content_type=None)
        return data["token"]

    async def _async_refresh(self) -> None:
        async with self._session.post(
            AUTH_REFRESH_URL,
            params={"key": FIREBASE_API_KEY},
            data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                _LOGGER.debug("token refresh failed (%s), logging in again", resp.status)
                await self.async_login()
                return
        self._store(data["id_token"], data["refresh_token"], data["expires_in"])

    def _store(self, id_token: str, refresh_token: str, expires_in: str | int) -> None:
        self._id_token = id_token
        self._refresh_token = refresh_token
        self._expires_at = time.monotonic() + int(expires_in)

    async def async_get_token(self, force_refresh: bool = False) -> str:
        """Return a valid ID token."""
        async with self._lock:
            if self._id_token is None:
                await self.async_login()
            elif force_refresh or time.monotonic() > self._expires_at - TOKEN_REFRESH_MARGIN:
                await self._async_refresh()
            assert self._id_token is not None
            return self._id_token

    @property
    def seconds_until_refresh(self) -> float:
        """Seconds until the current token should be refreshed."""
        return max(0.0, self._expires_at - TOKEN_REFRESH_MARGIN - time.monotonic())


class NoraClient:
    """Smart NORA client for one (account, group) pair."""

    def __init__(self, session: aiohttp.ClientSession, auth: NoraAuth, group: str) -> None:
        self._session = session
        self.auth = auth
        self.group = escape_firebase_path(group)
        self._offset_ms = 0

    # ---------------------------------------------------------------- API --
    def _headers(self, token: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {token}",
            "user-agent": f"{USER_AGENT}:{self.auth.uid}",
        }

    async def _async_post(self, path: str, body: Any, query: str = "") -> None:
        token = await self.auth.async_get_token()
        url = f"{API_ENDPOINT}/client/{path}?group={quote(self.group, safe='')}&{query}"
        payload = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
        headers = self._headers(token) | {
            "content-type": "application/json",
            "content-encoding": "gzip",
        }
        for attempt in range(4):
            async with self._session.post(url, data=payload, headers=headers) as resp:
                if 200 <= resp.status < 300:
                    return
                text = await resp.text()
                # same retry policy as shouldRetryRequest() in the plugin
                retry = resp.status == 429 or resp.status // 100 not in (2, 4)
                if not retry or attempt == 3:
                    raise NoraHttpError(resp.status, text)
            await asyncio.sleep(2**attempt)

    async def async_sync(self, devices: list[dict[str, Any]]) -> None:
        """Register the full device list of this group (Google SYNC)."""
        await self._async_post("sync", devices)

    async def async_update_state(self, device_id: str, state: dict[str, Any]) -> None:
        """Report the current state of one device (Google Report State)."""
        await self._async_post("update-state", state, f"id={quote(device_id, safe='')}")

    async def async_update_offset(self) -> None:
        """Get the server clock offset used for heartbeats."""
        token = await self.auth.async_get_token()
        async with self._session.get(
            f"{API_ENDPOINT}/client/offset",
            params={"timestamp": str(int(time.time() * 1000))},
            headers=self._headers(token),
        ) as resp:
            if resp.status != 200:
                raise NoraHttpError(resp.status, await resp.text())
            data = await resp.json(content_type=None)
        self._offset_ms = int(data.get("offset", 0))

    async def async_heartbeat(self) -> None:
        """Write the group heartbeat (user/<uid>/version/<group>/heartbeat)."""
        await self.async_db_put(
            f"user/{self.auth.uid}/version/{self.group}/heartbeat",
            int(time.time() * 1000) + self._offset_ms,
        )

    # ----------------------------------------------------------- Database --
    def _db_url(self, path: str) -> str:
        return f"{FIREBASE_DB_URL}/{path}.json"

    async def async_db_put(self, path: str, value: Any) -> None:
        """Set a value in the realtime database."""
        token = await self.auth.async_get_token()
        async with self._session.put(
            self._db_url(path), params={"auth": token}, json=value
        ) as resp:
            if resp.status != 200:
                raise NoraHttpError(resp.status, await resp.text())

    async def async_db_delete(self, path: str) -> None:
        """Remove a value from the realtime database."""
        token = await self.auth.async_get_token()
        async with self._session.delete(self._db_url(path), params={"auth": token}) as resp:
            if resp.status != 200:
                raise NoraHttpError(resp.status, await resp.text())

    async def async_stream(
        self,
        path: str,
        on_event: Callable[[str, str, Any], Awaitable[None] | None],
    ) -> None:
        """Stream a database path via Server-Sent Events.

        Calls on_event(event, path, data) for "put" and "patch" events.
        Returns when the token has to be renewed (auth_revoked or shortly
        before expiry); raises on connection errors. The caller loops.
        """
        token = await self.auth.async_get_token()
        timeout = aiohttp.ClientTimeout(total=None, sock_read=90)
        async with self._session.get(
            self._db_url(path),
            params={"auth": token},
            headers={"accept": "text/event-stream"},
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                raise NoraHttpError(resp.status, await resp.text())
            deadline = time.monotonic() + self.auth.seconds_until_refresh
            event: str | None = None
            async for raw in resp.content:
                line = raw.decode().rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event:
                    data_str = line[5:].strip()
                    if event in ("put", "patch"):
                        msg = json.loads(data_str)
                        result = on_event(event, msg["path"], msg["data"])
                        if asyncio.iscoroutine(result):
                            await result
                    elif event == "auth_revoked":
                        return
                    elif event == "cancel":
                        raise NoraError(f"stream {path} cancelled: {data_str}")
                    event = None
                if time.monotonic() > deadline:
                    return  # reconnect with a fresh token


def apply_stream_event(tree: dict[str, Any], event: str, path: str, data: Any) -> dict[str, Any]:
    """Apply a Firebase put/patch event to a local mirror; returns the new tree."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        if event == "put":
            return data if isinstance(data, dict) else {}
        if isinstance(data, dict):
            for key, value in data.items():
                tree = apply_stream_event(tree, "put", f"/{key}", value)
        return tree

    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    last = parts[-1]
    if event == "put":
        if data is None:
            node.pop(last, None)
        else:
            node[last] = data
    else:  # patch
        target = node.get(last)
        if not isinstance(target, dict):
            target = node[last] = {}
        for key, value in (data or {}).items():
            if value is None:
                target.pop(key, None)
            else:
                target[key] = value
    return tree
