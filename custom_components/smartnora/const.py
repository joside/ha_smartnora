"""Constants for the Smart NORA integration."""

from __future__ import annotations

DOMAIN = "smartnora"
VERSION = "0.1.0"

CONF_GROUP = "group"
CONF_ENTITIES = "entities"
DEFAULT_GROUP = "homeassistant"

# Public Firebase web configuration of Smart NORA (taken from
# node-red-contrib-smartnora/src/config.ts). Firebase web API keys are not
# secrets; access is controlled by Firebase Auth + database rules.
FIREBASE_API_KEY = "AIzaSyCE4ogvmNJG8Vvkzf1wfWKhjzCALlLGLsw"
FIREBASE_DB_URL = "https://nora-firebase-default-rtdb.europe-west1.firebasedatabase.app"
API_ENDPOINT = "https://api.smart-nora.eu"

AUTH_SIGNIN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
AUTH_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

USER_AGENT = f"ha-smartnora/{VERSION}"

HEARTBEAT_INTERVAL_SEC = 60  # HEARTBEAT_TIMEOUT_SEC in nora-firebase-common
STATE_DEBOUNCE_SEC = 0.5
SYNC_DEBOUNCE_SEC = 2.0

SUPPORTED_DOMAINS = (
    "scene",
    "script",
    "switch",
    "input_boolean",
    "light",
    "climate",
)
