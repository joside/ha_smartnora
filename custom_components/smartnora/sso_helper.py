"""Helper page to obtain a Smart NORA SSO token (Google sign-in accounts).

smart-nora.eu/my-nora/sso fetches the token from its own backend and sends it
to window.opener via postMessage({type: "nora-sso-token", email, token}, "*")
- the same mechanism the Node-RED config node uses. This page opens that
popup and shows the received token so it can be pasted into the config flow.
The token never passes through Home Assistant's backend.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, SSO_URL

HELPER_URL = "/api/smartnora/sso"

_PAGE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart NORA – SSO-Token</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#222}
 button{font-size:16px;padding:10px 18px;border:0;border-radius:6px;background:#1E5BD8;color:#fff;cursor:pointer}
 textarea{width:100%;height:110px;font-family:monospace;font-size:12px;margin-top:12px}
 .hint{color:#666;font-size:14px}
</style></head><body>
<h2>Smart NORA – SSO-Token</h2>
<p>1. Klicke auf <b>Anmelden</b>, melde dich im Popup mit Google an und bestätige.<br>
2. Kopiere das Token und füge es im Einrichtungsdialog von Home Assistant ein.</p>
<p class="hint">Click <b>Anmelden</b>, sign in with Google in the popup, then copy the token
into the Home Assistant setup dialog.</p>
<button id="login">Anmelden / Sign in</button>
<div id="result" hidden>
 <p>Token für / for <b id="email"></b>:</p>
 <textarea id="token" readonly></textarea>
 <p><button id="copy">Kopieren / Copy</button> <span id="copied" hidden>✓</span></p>
</div>
<script>
 const SSO_URL = "__SSO_URL__";
 document.getElementById("login").onclick = () =>
   window.open(SSO_URL, "_blank", "popup=yes,scrollbars=yes,resizable=yes,width=410,height=600");
 window.addEventListener("message", ev => {
   if (!ev.origin.startsWith("https://smart-nora.eu") &&
       !ev.origin.startsWith("https://nora-firebase.firebaseapp.com")) return;
   if (!ev.data || ev.data.type !== "nora-sso-token") return;
   document.getElementById("email").textContent = ev.data.email || "";
   document.getElementById("token").value = ev.data.token;
   document.getElementById("result").hidden = false;
 });
 document.getElementById("copy").onclick = async () => {
   const t = document.getElementById("token");
   try { await navigator.clipboard.writeText(t.value); } catch { t.select(); document.execCommand("copy"); }
   document.getElementById("copied").hidden = false;
 };
</script>
</body></html>
""".replace("__SSO_URL__", SSO_URL)


class SsoHelperView(HomeAssistantView):
    """Serves the static SSO helper page (contains no secrets)."""

    url = HELPER_URL
    name = "api:smartnora:sso"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(text=_PAGE, content_type="text/html")


def async_register_sso_helper(hass: HomeAssistant) -> None:
    """Register the helper page once."""
    key = f"{DOMAIN}_sso_helper"
    if not hass.data.get(key) and getattr(hass, "http", None) is not None:
        hass.http.register_view(SsoHelperView())
        hass.data[key] = True
