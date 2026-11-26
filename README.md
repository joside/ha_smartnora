# ha_smartnora

Home-Assistant-Integration für [Smart NORA](https://smart-nora.eu/): gibt HA-Entitäten an
Google Home / Google Assistant frei – ohne Node-RED und ohne öffentlich erreichbares Home Assistant.

> Status: **Prototyp (0.1.0)**, inoffiziell. Nachbau des Protokolls von
> [node-red-contrib-smartnora](https://github.com/andrei-tatar/node-red-contrib-smartnora) – siehe [PROTOCOL.md](PROTOCOL.md).

## Unterstützte Entitäten

| HA-Domain | Google-Gerät | Sprachbefehl (Beispiel) |
|---|---|---|
| `scene`, `script` | Szene | „Hey Google, Abend aktivieren“ |
| `switch`, `input_boolean` | Schalter | „… Kaffeemaschine einschalten“ |
| `light` | Leuchte (mit Helligkeit, falls dimmbar) | „… Wohnzimmer auf 40 %“ |
| `climate` | Thermostat (Modus, Solltemperatur) | „… Bad auf 22 Grad“ |

Raum = HA-Bereich der Entität (bzw. ihres Geräts), Name = Anzeigename.

## Installation

1. `custom_components/smartnora` nach `config/custom_components/` kopieren (oder per HACS als benutzerdefiniertes Repo).
2. HA neu starten → *Einstellungen → Geräte & Dienste → Integration hinzufügen → Smart NORA*.
3. Smart-NORA-Zugangsdaten eingeben. **Gruppe** frei wählen (Standard `homeassistant`), aber **nicht** die Gruppe aus Node-RED.
4. *Konfigurieren* → Entitäten auswählen. Nach dem Speichern synchronisiert die Integration mit Google.

## Parallelbetrieb mit Node-RED

Unterschiedliche Gruppen laufen parallel. Gleich benannte Geräte (z. B. eine Szene in Node-RED und in HA)
verwirren Google – Szenen also einzeln umziehen: in HA anlegen, freigeben, den Node-RED-Node entfernen.
Google-Routinen, die auf alte Geräte zeigen, müssen neu verknüpft werden.

## Noch nicht umgesetzt

Lokale Ausführung (UDP), SSO-Login, Farbe/Farbtemperatur bei Lichtern, Rollläden, Sensoren,
Benachrichtigungen, Zwei-Faktor (PIN), Namen/Räume per Optionen überschreiben.
