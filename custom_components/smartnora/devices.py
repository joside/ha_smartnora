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
        # entity registry capabilities: available even while the entity is unavailable
        self.capabilities: dict[str, Any] = {}

    @property
    def ha_state(self) -> State | None:
        return self.hass.states.get(self.entity_id)

    def attr(self, key: str, default: Any = None) -> Any:
        """State attribute, falling back to the registry capabilities."""
        st = self.ha_state
        value = st.attributes.get(key) if st else None
        if value is None:
            value = self.capabilities.get(key)
        return default if value is None else value

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


COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}


class LightAdapter(OnOffAdapter):
    """Light with optional Brightness and ColorSetting (RGB, like nora-light colortype=rgb)."""

    google_type = "action.devices.types.LIGHT"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_brightness = 100
        self._last_rgb = 0xFFFFFF

    def _modes(self) -> set[str]:
        return set(self.attr("supported_color_modes", []))

    def _dimmable(self) -> bool:
        return any(m != "onoff" for m in self._modes())

    def _colored(self) -> bool:
        return bool(self._modes() & COLOR_MODES)

    def traits_for(self):
        traits = ["action.devices.traits.OnOff"]
        if self._dimmable():
            traits.append("action.devices.traits.Brightness")
        if self._colored():
            traits.append("action.devices.traits.ColorSetting")
        return traits

    def attributes(self):
        if self._colored():
            return {"colorModel": "rgb", "commandOnlyColorSetting": False}
        return {}

    def nora_specific(self):
        spec = {}
        if self._dimmable():
            spec["turnOnWhenBrightnessChanges"] = True
        if self._colored():
            spec["turnOnWhenColorChanges"] = True
        return spec

    def state(self):
        state = super().state()
        st = self.ha_state
        if self._dimmable():
            raw = st.attributes.get("brightness") if st else None
            if raw:  # HA reports None while off -> keep the last value
                self._last_brightness = max(1, round(raw / 255 * 100))
            state["brightness"] = self._last_brightness
        if self._colored():
            rgb = st.attributes.get("rgb_color") if st else None
            if rgb:
                r, g, b = (int(c) for c in rgb[:3])
                self._last_rgb = (r << 16) + (g << 8) + b
            state["color"] = {"spectrumRgb": self._last_rgb}
        return state

    def commands_for(self, old, new):
        on = new.get("on", old.get("on"))
        if not on:
            return super().commands_for(old, new)

        data: dict[str, Any] = {ATTR_ENTITY_ID: self.entity_id}
        bri = new.get("brightness")
        if bri is not None and bri != old.get("brightness"):
            data["brightness_pct"] = bri
        rgb = (new.get("color") or {}).get("spectrumRgb")
        if rgb is not None and rgb != (old.get("color") or {}).get("spectrumRgb"):
            data["rgb_color"] = [(rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF]
            # a colour request means a solid colour (as the Node-RED flows did with fx: 0)
            if "Solid" in self.attr("effect_list", []):
                data["effect"] = "Solid"
        if len(data) > 1 or not old.get("on"):
            return [Command("light", "turn_on", data)]
        return []


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
        modes = [HVAC_TO_GOOGLE[m] for m in self.attr("hvac_modes", []) if m in HVAC_TO_GOOGLE]
        attrs: dict[str, Any] = {
            "availableThermostatModes": modes or ["off", "heat"],
            "thermostatTemperatureUnit": "C",
        }
        if self.attr("min_temp") is not None and self.attr("max_temp") is not None:
            attrs["thermostatTemperatureRange"] = {
                "minThresholdCelsius": float(self.attr("min_temp")),
                "maxThresholdCelsius": float(self.attr("max_temp")),
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
