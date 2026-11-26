# Smart NORA – Protokoll-Analyse

Analysiert aus `node-red-contrib-smartnora` (Commit `e0ebbac`, 2026-11-03) und
`@andrei-tatar/nora-firebase-common` 1.14.1. Dateiverweise beziehen sich auf das Plugin-Repo.

## Überblick

```
Google Home ──► Smart NORA Cloud (Firebase) ◄──► Client (Node-RED / ha_smartnora)
                    │                               │
                    ├─ Realtime Database  ◄─────────┤  Streams lesen, Heartbeat schreiben
                    └─ api.smart-nora.eu  ◄─────────┘  sync / update-state (HTTP POST)
```

Der Client baut **nur ausgehende Verbindungen** auf. Keine Portfreigabe, kein öffentlicher Zugang nötig.
Google-Befehle führt der **Server** aus (mit `executeCommand` aus nora-firebase-common) und schreibt
den neuen Zustand in die Datenbank; der Client reagiert darauf.

## Konfiguration (`src/config.ts`)

| Wert | Inhalt |
|---|---|
| Firebase-Projekt | `nora-firebase` |
| API-Key (öffentlich, Web-Config) | `AIzaSyCE4ogvmNJG8Vvkzf1wfWKhjzCALlLGLsw` |
| Realtime DB | `https://nora-firebase-default-rtdb.europe-west1.firebasedatabase.app` |
| HTTP-API | `https://api.smart-nora.eu` |

## 1. Anmeldung (`src/nora/connection.ts`)

- **E-Mail/Passwort**: Firebase `signInWithEmailAndPassword`.
  REST-Äquivalent: `POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=<API_KEY>`
  mit `{email, password, returnSecureToken: true}` → `idToken`, `refreshToken`, `expiresIn` (3600 s), `localId` (= uid).
- Token-Erneuerung: `POST https://securetoken.googleapis.com/v1/token?key=<API_KEY>`, `grant_type=refresh_token`.
- **SSO** (Alternative): `POST https://api.smart-nora.eu/sso/exchange` mit `{token}` → Custom-Token → `signInWithCustomToken`.
  Das SSO-Token stammt von `https://smart-nora.eu/my-nora/sso`: Die Seite holt es mit der Web-Sitzung über
  `GET api/sso/token` und schickt es per `window.opener.postMessage({type: 'nora-sso-token', email, token}, '*')`
  an das öffnende Fenster (ohne `window.opener` leitet sie weg). Das Token ist dauerhaft und wird bei jeder
  Anmeldung neu getauscht. REST: `POST identitytoolkit…/accounts:signInWithCustomToken?key=…` mit
  `{token, returnSecureToken: true}`; die uid steht im ID-Token (`user_id`). ha_smartnora liefert dafür die
  Hilfsseite `/api/smartnora/sso`.
- Bei Fehlern: neuer Versuch nach 30–90 s (zufällig).

## 2. Gruppe

- `group` aus dem Config-Node, Firebase-Sonderzeichen `. # $ [ ]` → `:` (`escapeFirebasePath`).
- Eine Gruppe = eine Client-Instanz. Ein SYNC ersetzt die Geräteliste **dieser Gruppe**.
  → HA muss eine **andere Gruppe** als Node-RED verwenden, dann laufen beide parallel.
- Geräte-ID in Google: `<group>|<deviceId>`; Node-RED nutzt die Node-ID, ha_smartnora die escapte entity_id
  (`scene.abend` → `scene:abend`).

## 3. Datenbank-Pfade (`src/nora/sync.ts`, `device.ts`, `scene-device.ts`)

| Pfad | Richtung | Zweck |
|---|---|---|
| `device_states/<uid>/<group>/<id>` | Server → Client | `{state: {...}, update: {by: 'server'\|'client', timestamp}}` |
| `device_nora/<uid>/<group>/<id>` | Server → Client | NORA-Zusatzdaten, `error: {msg, details}` |
| `device_nora/<uid>/<group>/<id>/pendingScene` | Server → Client | `{deactivate: bool}`; Client **löscht** den Eintrag und führt die Szene aus |
| `user/<uid>/version/<group>/heartbeat` | Client → Server | Zeitstempel (ms, um Server-Offset korrigiert), alle 60 s |
| `user/<uid>/actions/<identifier>` | Server → Client | Aktionen aus Benachrichtigungen (nur notify-Node) |
| `.info/connected` | – | Verbindungsstatus des JS-SDK (gibt es per REST nicht) |

Befehlserkennung (FirebaseDevice.stateUpdates$): Zustand aus `device_states` übernehmen, wenn
`update.by !== 'client'` und sich `update.timestamp` geändert hat.

Per REST: `GET <db>/<pfad>.json?auth=<idToken>` mit `Accept: text/event-stream` liefert
`put`/`patch`-Events, `keep-alive`, `auth_revoked` (Token abgelaufen → neu verbinden), `cancel` (Rechte).

## 4. HTTP-API (`src/nora/sync.ts`, `fetch.ts`)

Alle Aufrufe: `Authorization: Bearer <idToken>`, Body **gzip-komprimiertes JSON**
(`content-encoding: gzip`), Query `group=<group>`.

| Aufruf | Body | Wann |
|---|---|---|
| `POST /client/sync?group=…` | Liste aller Geräte (Definition inkl. state) | Start und bei Änderung der Definitionen (Hash ohne state) |
| `POST /client/update-state?group=…&id=<id>` | kompletter state des Geräts | bei lokaler Zustandsänderung |
| `POST /client/notify?group=…` | WebpushNotification | notify-Node |
| `POST /client/home-notify?group=…&id=<id>` | ObjectDetectionNotification | z. B. Türklingel |
| `GET /client/offset?timestamp=<ms>` | – | `{offset}` für den Heartbeat |

Wiederholung: bei 429 und 5xx bis 3×, bei 4xx nicht. Rate-Limit im Client: 12 Jobs pro Minute je
Job-Typ bzw. je Gerät für update-state (Jobs werden zusammengeführt).

## 5. Gerätedefinition (Google Smart Home Schema)

```json
{
  "id": "scene:abend",
  "type": "action.devices.types.SCENE",
  "traits": ["action.devices.traits.Scene"],
  "name": {"name": "Abend"},
  "roomHint": "Wohnzimmer",
  "willReportState": true,
  "attributes": {"sceneReversible": false},
  "state": {"online": true},
  "noraSpecific": {}
}
```

`roomHint` muss **immer** ein String sein (notfalls `""`): Das Dashboard auf smart-nora.eu ruft
`config.roomHint.trim()` auf und bleibt sonst auf „Loading...“ hängen.

`noraSpecific` steuert Server-Verhalten, u. a. `twoFactor`, `asyncCommandExecution`,
`returnOnOffErrorCodeIfStateAlreadySet`, `turnOnWhenBrightnessChanges`. Gültige Zustände prüft
nora-firebase-common per JSON-Schema (`validate`), auch serverseitig.

## 6. Lokale Ausführung (`src/nora/local-execution.ts`)

Google-Lautsprecher senden UDP-Discovery (Port 6988), der Client antwortet (6989) und nimmt Befehle
auf Port 6987 an. Optional, nur für geringere Latenz. Ohne Antwort nutzt Google den Cloud-Weg.
Im Prototyp nicht umgesetzt.

## Offene Punkte / Risiken

- Kein offizielles API. Änderungen am Backend können die Integration brechen.
- Ob der Server den Heartbeat für den Online-Status braucht, ist nicht dokumentiert; ha_smartnora sendet ihn wie das Plugin.
- User-Agent: ha_smartnora meldet sich ehrlich als `ha-smartnora/<version>:<uid>`.
- Vor einer Veröffentlichung mit Andrei Tatar abstimmen.
