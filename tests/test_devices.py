"""Tests for the HA -> Google device mapping."""

from homeassistant.core import HomeAssistant

from custom_components.smartnora.devices import (
    LightAdapter,
    OnOffAdapter,
    SceneAdapter,
    ThermostatAdapter,
)


async def test_switch(hass: HomeAssistant) -> None:
    hass.states.async_set("input_boolean.gast", "on", {"friendly_name": "Gast"})
    adapter = OnOffAdapter(hass, "input_boolean.gast", "Gast", "Flur")
    definition = adapter.definition()
    assert definition["id"] == "input_boolean:gast"
    assert definition["roomHint"] == "Flur"
    assert definition["state"] == {"online": True, "on": True}
    cmds = adapter.commands_for({"on": True}, {"on": False, "online": True})
    assert [(c.domain, c.service) for c in cmds] == [("input_boolean", "turn_off")]


async def test_scene(hass: HomeAssistant) -> None:
    hass.states.async_set("script.abend", "off")
    adapter = SceneAdapter(hass, "script.abend", "Abend", None)
    assert adapter.definition()["roomHint"] == ""
    assert adapter.definition()["type"] == "action.devices.types.SCENE"
    cmd = adapter.activate(deactivate=False)
    assert (cmd.domain, cmd.service) == ("script", "turn_on")
    assert adapter.activate(deactivate=True) is None


async def test_light_dimmable(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "light.wled", "on", {"supported_color_modes": ["rgb"], "brightness": 128}
    )
    adapter = LightAdapter(hass, "light.wled", "WLED", None)
    assert "action.devices.traits.Brightness" in adapter.traits_for()
    assert adapter.state()["brightness"] == 50
    cmds = adapter.commands_for({"on": True, "brightness": 50}, {"on": True, "brightness": 20})
    assert cmds[0].data["brightness_pct"] == 20


async def test_light_onoff_only(hass: HomeAssistant) -> None:
    hass.states.async_set("light.flur", "off", {"supported_color_modes": ["onoff"]})
    adapter = LightAdapter(hass, "light.flur", "Flur", None)
    assert adapter.traits_for() == ["action.devices.traits.OnOff"]
    assert "brightness" not in adapter.state()


async def test_thermostat(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "climate.bad",
        "heat",
        {"hvac_modes": ["off", "heat"], "temperature": 21, "current_temperature": 19.5,
         "min_temp": 7, "max_temp": 30},
    )
    adapter = ThermostatAdapter(hass, "climate.bad", "Bad", None)
    assert adapter.attributes()["availableThermostatModes"] == ["off", "heat"]
    assert adapter.state()["thermostatTemperatureAmbient"] == 19.5
    cmds = adapter.commands_for(adapter.state(), {"thermostatMode": "off", "thermostatTemperatureSetpoint": 23})
    assert [(c.service) for c in cmds] == ["set_hvac_mode", "set_temperature"]
    assert cmds[0].data["hvac_mode"] == "off"


async def test_reversible_script(hass: HomeAssistant) -> None:
    hass.states.async_set("script.kamin", "off")
    adapter = SceneAdapter(hass, "script.kamin", "Kamin", "Wohnzimmer")
    adapter.reversible = True
    assert adapter.attributes() == {"sceneReversible": True}
    cmd = adapter.activate(deactivate=True)
    assert (cmd.domain, cmd.service) == ("script", "turn_on")
    assert cmd.data["variables"] == {"deactivate": True}


async def test_light_color(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "light.wled_2", "on",
        {"supported_color_modes": ["rgb"], "brightness": 255, "rgb_color": [255, 0, 0],
         "effect_list": ["Solid", "Fire 2012"], "effect": "Fire 2012"},
    )
    adapter = LightAdapter(hass, "light.wled_2", "Stripe", "Büro")
    assert "action.devices.traits.ColorSetting" in adapter.traits_for()
    assert adapter.attributes() == {"colorModel": "rgb", "commandOnlyColorSetting": False}
    old = adapter.state()
    assert old["color"] == {"spectrumRgb": 0xFF0000}
    cmds = adapter.commands_for(old, {**old, "color": {"spectrumRgb": 0x0000FF}})
    assert cmds[0].service == "turn_on"
    assert cmds[0].data["rgb_color"] == [0, 0, 255]
    assert cmds[0].data["effect"] == "Solid"


async def test_light_keeps_color_while_off(hass: HomeAssistant) -> None:
    hass.states.async_set("light.wled", "on", {"supported_color_modes": ["rgb"], "brightness": 128, "rgb_color": [0, 255, 0]})
    adapter = LightAdapter(hass, "light.wled", "Streifen", None)
    adapter.state()
    hass.states.async_set("light.wled", "off", {"supported_color_modes": ["rgb"]})
    state = adapter.state()
    assert state["on"] is False
    assert state["brightness"] == 50
    assert state["color"] == {"spectrumRgb": 0x00FF00}
    cmds = adapter.commands_for(state, {**state, "on": True})
    assert (cmds[0].service, cmds[0].data) == ("turn_on", {"entity_id": "light.wled"})
