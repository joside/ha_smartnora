"""Mapping between Home Assistant entities and Smart NORA (Google) devices.

Each adapter builds the device definition used for SYNC, derives the NORA
state from the HA state, and turns server-side state changes (Google
EXECUTE results written by Smart NORA) back into HA service calls.

Device definitions mirror the Node-RED nodes (nora-switch.ts, nora-scene.ts,
nora-light.ts, nora-thermostat.ts).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State

from .api import escape_firebase_path

# HA hvac_mode -> Google thermostatMode
HVAC_TO_GOOGLE = {
    "off": "off",
    "heat": "heat",
    "cool": "cool",
    "heat_cool": "heatcool",
    "auto": "auto",
    "fan_only": "fan-only",
    "dry": "dry",
}
GOOGLE_TO_HVAC = {v: k for k, v in HVAC_TO_GOOGLE.items()}


def _online(state: State | None) -> bool:
    return state is not None and state.state != STATE_UNAVAILABLE


@dataclass
class Command:
    """A Home Assistant service call."""

    domain: str
    service: str
    data: dict[str, Any]


class Adapter:
    """Base adapter for one exposed entity."""

    google_type: str = ""
    traits: list[str] = []
    is_scene = False

    def __init__(self, hass: HomeAssistant, entity_id: str, name: str, room: str | None) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.device_id = escape_firebase_path(entity_id)
        self.name = name
        self.room = room

    @property
    def ha_state(self) -> State | None:
        return self.hass.states.get(self.entity_id)

    def attributes(self) -> dict[str, Any]:
        return {}

    def traits_for(self) -> list[str]:
        return list(self.traits)

    def nora_specific(self) -> dict[str, Any]:
        return {}

    def state(self) -> dict[str, Any]:
        return {"online": _online(self.ha_state)}

    def definition(self) -> dict[str, Any]:
        device: dict[str, Any] = {
            "id": self.device_id,
            "type": self.google_type,
            "traits": self.traits_for(),
            "name": {"name": self.name},
            # always a string: the smart-nora.eu dashboard calls roomHint.trim()
            "roomHint": self.room or "",
            "willReportState": True,
            "attributes": self.attributes(),
            "state": self.state(),
            "noraSpecific": self.nora_specific(),
        }
        return device

    def commands_for(self, old: dict[str, Any], new: dict[str, Any]) -> list[Command]:
        """Service calls needed to move HA from state `old` to `new`."""
        return []

    def activate(self, deactivate: bool) -> Command | None:
        """Scene activation (only scene adapters)."""
        return None


class OnOffAdapter(Adapter):
    google_type = "action.devices.types.SWITCH"
    traits = ["action.devices.traits.OnOff"]

    def state(self) -> dict[str, Any]:
        st = self.ha_state
        return {"online": _online(st), "on": st is not None and st.state == STATE_ON}

    def commands_for(self, old, new):
        if "on" in new and new.get("on") != old.get("on"):
            domain = self.entity_id.split(".")[0]
            service = "turn_on" if new["on"] else "turn_off"
            return [Command(domain, service, {ATTR_ENTITY_ID: self.entity_id})]
        return []


class LightAdapter(OnOffAdapter):
    google_type = "action.devices.types.LIGHT"

    def _dimmable(self) -> bool:
        st = self.ha_state
        modes = (st.attributes.get("supported_color_modes") or []) if st else []
        return any(m != "onoff" for m in modes)

    def traits_for(self):
        traits = ["action.devices.traits.OnOff"]
        if self._dimmable():
            traits.append("action.devices.traits.Brightness")
        return traits

    def nora_specific(self):
        return {"turnOnWhenBrightnessChanges": True} if self._dimmable() else {}

    def state(self):
        state = super().state()
        if self._dimmable():
            st = self.ha_state
            raw = st.attributes.get("brightness") if st else None
            # keep last known brightness while off (HA reports None)
            state["brightness"] = round(raw / 255 * 100) if raw else 100
        return state

    def commands_for(self, old, new):
        on = new.get("on", old.get("on"))
        bri = new.get("brightness")
        if on and bri is not None and bri != old.get("brightness"):
            return [Command("light", "turn_on", {
                ATTR_ENTITY_ID: self.entity_id, "brightness_pct": bri,
            })]
        return super().commands_for(old, new)


class SceneAdapter(Adapter):
    """HA scene or script exposed as Google scene ("<Name> aktivieren")."""

    google_type = "action.devices.types.SCENE"
    traits = ["action.devices.traits.Scene"]
    is_scene = True

    #: set for scripts that declare a ``deactivate`` field
    reversible = False

    def attributes(self):
        return {"sceneReversible": self.reversible}

    def activate(self, deactivate):
        domain = self.entity_id.split(".")[0]
        if self.reversible:
            return Command("script", "turn_on", {
                ATTR_ENTITY_ID: self.entity_id,
                "variables": {"deactivate": deactivate},
            })
        if deactivate:
            return None
        return Command(domain, "turn_on", {ATTR_ENTITY_ID: self.entity_id})


class ThermostatAdapter(Adapter):
    google_type = "action.devices.types.THERMOSTAT"
    traits = ["action.devices.traits.TemperatureSetting"]

    def attributes(self):
        st = self.ha_state
        modes = [HVAC_TO_GOOGLE[m] for m in (st.attributes.get("hvac_modes", []) if st else [])
                 if m in HVAC_TO_GOOGLE]
        attrs: dict[str, Any] = {
            "availableThermostatModes": modes or ["off", "heat"],
            "thermostatTemperatureUnit": "C",
        }
        if st and st.attributes.get("min_temp") is not None and st.attributes.get("max_temp") is not None:
            attrs["thermostatTemperatureRange"] = {
                "minThresholdCelsius": float(st.attributes["min_temp"]),
                "maxThresholdCelsius": float(st.attributes["max_temp"]),
            }
        return attrs

    def state(self):
        st = self.ha_state
        setpoint = float(st.attributes.get("temperature") or 20) if st else 20.0
        ambient = st.attributes.get("current_temperature") if st else None
        return {
            "online": _online(st),
            "thermostatMode": HVAC_TO_GOOGLE.get(st.state, "off") if st else "off",
            "thermostatTemperatureSetpoint": setpoint,
            "thermostatTemperatureAmbient": float(ambient) if ambient is not None else setpoint,
        }

    def commands_for(self, old, new):
        cmds = []
        mode = new.get("thermostatMode")
        if mode and mode != old.get("thermostatMode") and mode in GOOGLE_TO_HVAC:
            cmds.append(Command("climate", "set_hvac_mode", {
                ATTR_ENTITY_ID: self.entity_id, "hvac_mode": GOOGLE_TO_HVAC[mode],
            }))
        sp = new.get("thermostatTemperatureSetpoint")
        if sp is not None and sp != old.get("thermostatTemperatureSetpoint"):
            cmds.append(Command("climate", "set_temperature", {
                ATTR_ENTITY_ID: self.entity_id, "temperature": sp,
            }))
        return cmds


ADAPTERS: dict[str, type[Adapter]] = {
    "switch": OnOffAdapter,
    "input_boolean": OnOffAdapter,
    "light": LightAdapter,
    "scene": SceneAdapter,
    "script": SceneAdapter,
    "climate": ThermostatAdapter,
}
