# liveticker

Minimaler Kurs-Sammler für einen zweiten Rechner (z. B. Raspberry Pi).
Liest Realtime-Kurse von einer Webseite und streamt sie in eine laufende
Trading-App-Instanz.

Zwei Abhängigkeiten: **selenium** und **websockets**. Kein Streamlit, kein
pandas, kein plotly — die Daten gehen über Streamlits eigenen WebSocket, dessen
eine Nachricht dieses Paket von Hand kodiert (`stream.py`).

```
Pi:  Chromium ──scrape──► liveticker ──websocket──► Server: Streamlit-App ──► ticker_data.db
```

## Installation auf dem Raspberry Pi

Getestet mit Raspberry Pi OS (Bookworm, 64 Bit) und Python 3.11.

```bash
sudo apt update
sudo apt install -y chromium-browser chromium-chromedriver python3-venv

python3 -m venv ~/liveticker-venv
~/liveticker-venv/bin/pip install /pfad/zu/liveticker-pi
```

Prüfen, ob Browser und Treiber gefunden werden:

```bash
which chromium-browser chromedriver
```

Auf 64-Bit-Raspberry-Pi-OS heißen die Pakete teils `chromium` und
`chromium-driver` — dann die Pfade in der Konfiguration setzen (siehe unten).

### Speicherbedarf

Chromium ist der teure Teil, nicht dieses Paket. Empfehlung: **Pi 4 oder 5 mit
≥ 2 GB RAM** und aktiviertem Swap. Ein Pi Zero 2 W reicht nicht zuverlässig —
Chromium mit einer werbelastigen Seite belegt typischerweise 400–700 MB.

### ⚠ Headless zuerst prüfen

Der Standard ist `headless = true`. **In einem Test auf Windows lieferte die
Quelle an ein Headless-Chrome keine Kurstabelle** (`table missing: …`), an ein
sichtbares Fenster dagegen alle 15 Symbole. Ob Chromium auf dem Pi sich anders
verhält, muss dort gemessen werden — deshalb als Erstes:

```bash
liveticker --dry-run --once --log INFO
```

Kommt eine Zeile `dry run — would send 15 quotes: …`, ist alles gut.
Erscheint stattdessen `table missing`, gibt es zwei Wege:

```bash
# a) virtueller Bildschirm (zuverlässig, ~20 MB extra):
sudo apt install -y xvfb
# in der ini: headless = false
xvfb-run -a --server-args="-screen 0 1280x1024x24" liveticker
#   (im systemd-Dienst entsprechend ExecStart anpassen)

# b) User-Agent setzen und Headless erneut versuchen:
#    user_agent = Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 …
```

Der systemd-Dienst enthält für Variante (a) eine auskommentierte ExecStart-Zeile.

## Konfiguration

Reihenfolge: **CLI > Umgebungsvariable > ini-Datei > Standard**.

Datei `~/.config/liveticker.ini` oder `/etc/liveticker.ini` (Vorlage:
`liveticker.ini.example`):

```ini
[liveticker]
target      = http://192.168.1.10:8080
api_key     = DEIN_SCHLUESSEL
fetch_type  = indices
headless    = true
; nur nötig, wenn die Pfade abweichen:
chrome_binary = /usr/bin/chromium-browser
chromedriver  = /usr/bin/chromedriver
start_time    = 06:00
end_time      = 21:59
cycle_seconds = 20
```

Jede Einstellung geht auch als Umgebungsvariable: `LIVETICKER_TARGET`,
`LIVETICKER_API_KEY`, `LIVETICKER_HEADLESS`, …

Der `api_key` muss einem in der App konfigurierten Schlüssel entsprechen
(`config.db`, Eintrag `<user>:api_key`). Er reist im Nachrichtenrumpf, nicht in
einer URL.

## Betrieb

```bash
liveticker --help
liveticker --dry-run --once --log DEBUG      # Seite lesen, nichts senden
liveticker --anytime --once                  # ein Zyklus, auch außerhalb der Handelszeit
liveticker                                   # Dauerbetrieb
```

Ohne `--anytime` arbeitet der Sammler nur **Mo–Fr zwischen `start_time` und
`end_time`**. Außerhalb dieser Zeit wird die Seite nicht abgerufen und der
Browser **geschlossen** — am Wochenende läuft kein Chromium.

### Als Dienst (systemd)

```bash
sudo cp systemd/liveticker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now liveticker
journalctl -u liveticker -f
```

## Was der Sammler an der Seite abräumt

Alles automatisch, alles gegen die echte Seite verifiziert:

| Hürde | Behandlung |
|---|---|
| Contentpass-Wand (`cp.finanzen.net/first-layer`, iframe ohne id/class) | „Einwilligen & weiter" im iframe |
| Google-Anmeldung (`div#onetap-container`) | Schließen-Button / `google.accounts.id.cancel()` |
| Push-Opt-in | „Später entscheiden" (case-insensitiv) |
| Chrome-Berechtigungsdialoge | per Profil-Einstellung blockiert |
| Werbe-Overlays | gezielter Close-Durchlauf, nur innerhalb schwebender Ebenen |

**Was er niemals klickt:** „Ablehnen & abonnieren" (3,99 €/Monat),
„Benachrichtigungen aktivieren", Logins und alles mit
kaufen/registrieren/zahlungs im Text. Die Sperrliste greift in beiden
Klickpfaden (Python und JavaScript) — siehe `FORBIDDEN_CLICK_TEXT` in
`scraper.py`.

## Datenqualität

- Deutsche Zahlformate werden zu Floats geparst (`24.004,02` → `24004.02`);
  unparsbare Zellen werden verworfen, nicht als Text weitergereicht.
- **Zeitstempel werden vollständig gesendet** (`YYYY-MM-DD HH:MM:SS`), nicht als
  nackte Uhrzeit. Grund: Die Quelle rendert ihre Zeiten serverseitig in **MESZ**
  und rechnet sie erst per JavaScript in die Browser-Zeitzone um — eine gerade
  geladene Seite zeigt daher für einige Sekunden gemischte Zonen. Läuft der Pi
  in einer anderen Zone (z. B. Kanaren, UTC+1), sieht ein Empfänger solche
  Kurse „in der Zukunft" und datiert sie auf **gestern** zurück. Der Sammler
  wählt deshalb selbst das Datum, das der aktuellen Zeit am nächsten liegt.
  `source_timezone` (IANA-Name) erzwingt zusätzlich eine echte Umrechnung —
  nur sinnvoll bei Quellen, die **nie** selbst umrechnen.
- Kurssprünge über 10 % müssen im nächsten Zyklus bestätigt werden.
- Es werden nur **geänderte** Kurse gesendet.
- Die App bestätigt jede Sendung mit `Success: <gespeichert>/<gesendet>`; bleibt
  die Bestätigung aus, gilt der Zyklus als fehlgeschlagen und wird wiederholt.

## Selbstheilung

Bei fehlender Tabelle, eingefrorenen Kursen (5 Zyklen) oder zu geringer
Trefferquote läuft eine Eskalationsleiter: Overlays schließen → Neuladen →
Neu-Navigation → Browser-Neustart. Jedes WebDriver-Kommando hat eine harte
Frist (45 s); bei Überschreitung wird der Browser-Prozessbaum abgeschossen,
damit ein hängender Renderer den Dienst nicht blockiert.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

50 Tests, ohne Browser und ohne Netz. Ist Streamlit zufällig installiert,
vergleicht ein Test den handkodierten Protobuf-Rahmen byteweise mit Streamlits
eigener Serialisierung.

## Grenzen

- **Nur WebSocket-Transport.** Der Browser-Fallback der großen Fassung fehlt
  bewusst: er bräuchte einen zweiten Chromium, und genau den kann ein Pi nicht
  entbehren. Ist der WebSocket blockiert (Proxy, TLS-Terminierung), scheitert
  die Zustellung sichtbar im Log.
- `/_stcore/stream` und das `BackMsg`-Format sind **Streamlit-Interna**. Nach
  einem Streamlit-Update auf dem Server bitte einen `--dry-run`-freien Testlauf
  machen und im Log auf `Success:` achten.
- Die Seitenstruktur (Tabellenspalten, Sektionsüberschriften) steckt in
  `scraper.py` und `symbols.py`. Ändert die Quelle ihr Layout, muss dort
  nachgezogen werden.
