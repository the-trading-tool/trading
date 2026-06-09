# Trading-App — Erstinstallation

---

## ⚖️ Rechtlicher Hinweis / Legal Disclaimer

**DE** — Diese App ist ein persönliches Werkzeug für den privaten, nicht-kommerziellen Gebrauch.
Sie enthält selbst **keine Marktdaten**. Kursdaten werden zur Laufzeit von Drittanbietern
(z. B. Yahoo Finance, Financial Modeling Prep) abgerufen. Jeder Nutzer ist **selbst verantwortlich**
für die Einhaltung der jeweiligen Nutzungsbedingungen dieser Anbieter — insbesondere der
[Yahoo Finance Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) und der
[FMP Terms of Service](https://financialmodelingprep.com/terms-of-service).
Die App dient ausschließlich zu Informations- und Analysezwecken und stellt
**keine Anlage- oder Finanzberatung** dar. Handlungen auf Basis der angezeigten Daten
erfolgen auf eigene Verantwortung und eigenes Risiko. Der Autor übernimmt keinerlei Haftung
für Schäden, die durch die Nutzung dieser Software oder der darüber abgerufenen Daten entstehen.

**EN** — This app is a personal tool for private, non-commercial use.
It contains **no market data** of its own. Price data is fetched at runtime from third-party
providers (e.g. Yahoo Finance, Financial Modeling Prep). Each user is **solely responsible**
for complying with the terms of service of those providers — in particular the
[Yahoo Finance Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) and the
[FMP Terms of Service](https://financialmodelingprep.com/terms-of-service).
The app is provided for informational and analytical purposes only and does
**not constitute investment or financial advice**. Any actions taken based on the
information displayed are at the user's own responsibility and risk. The author accepts
no liability for any damages arising from the use of this software or the data retrieved through it.

> **Lizenz / License:** Nur privater, nicht-kommerzieller Einsatz gestattet.
> Commercial use is not permitted.

---

Diese Anleitung erklärt Schritt für Schritt, wie du die Trading-App auf einem neuen PC installierst und startest.

- [Windows-Installation](#windows-installation)
- [Linux-Installation](#linux-installation)

---

---

## Windows-Installation

## Was du brauchst

- Windows 10 oder 11
- Internetverbindung (zwingend erforderlich — die App lädt Kursdaten von Yahoo Finance)
- Ca. 30–60 Minuten Zeit (davon ~20 Min. für den Daten-Download)

---

## Schritt 1 — Python installieren

1. Öffne deinen Browser und gehe auf:
   **https://www.python.org/downloads/**

2. Klicke auf den großen gelben Button **„Download Python 3.11.x"**

3. Starte die heruntergeladene Datei (`python-3.11.x-amd64.exe`)

4. **WICHTIG:** Aktiviere ganz unten das Häkchen **„Add python.exe to PATH"**  
   *(ohne dieses Häkchen funktioniert die Installation nicht!)*

5. Klicke auf **„Install Now"** und warte bis die Installation abgeschlossen ist

6. Klicke auf **„Close"**

---

## Schritt 2 — Code herunterladen

1. Öffne deinen Browser und gehe auf:
   **https://github.com/online-junkie/trading**

2. Klicke auf den grünen Button **„Code"**

3. Klicke auf **„Download ZIP"**

4. Entpacke die ZIP-Datei an einen Ort deiner Wahl, z.B. `C:\Trading`

5. Öffne den entpackten Ordner — du siehst darin einen Unterordner **„Trading"**  
   Wechsle in diesen Unterordner (dort liegt die Datei `asset_analyzer.py`)

---

## Schritt 3 — Installation ausführen

1. Halte die **Shift-Taste** gedrückt und klicke mit der **rechten Maustaste** in den Ordner (auf eine leere Stelle)

2. Wähle **„PowerShell-Fenster hier öffnen"**  
   *(oder: „In Terminal öffnen")*

3. Gib folgenden Befehl ein und drücke Enter:

   ```
   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
   ```

4. Gib dann folgenden Befehl ein und drücke Enter:

   ```
   .\install.ps1
   ```

5. Das Skript startet jetzt und arbeitet automatisch. Du siehst laufende Meldungen in blauer, grüner und grauer Schrift.  
   **Bitte warte, das kann einige Minuten dauern.**

---

## Schritt 4 — Admin-Benutzer anlegen

Das Skript fragt dich nach einem Benutzernamen und Passwort für die App:

```
Admin-Benutzername (Standard: admin):
```
→ Drücke einfach Enter um „admin" zu übernehmen, oder tippe einen eigenen Namen

```
E-Mail-Adresse (Standard: admin@localhost):
```
→ Drücke Enter oder gib deine E-Mail-Adresse ein

```
Passwort:
Passwort bestätigen:
```
→ Tippe ein Passwort (mindestens 8 Zeichen), das Passwort wird **nicht angezeigt** — das ist normal

Am Ende siehst du:

```
===========================================
 Installation abgeschlossen!
===========================================
```

---

## Schritt 5 — Daten laden (Pflicht!)

> ⚠️ **Ohne diesen Schritt zeigt die App keine Daten an.**

Die App benötigt Ticker-Stammdaten von Yahoo Finance. Gib im PowerShell-Fenster ein:

```
.\install.ps1 -InitInfo
```

Das Script lädt Metadaten (Name, Sektor, Branche) für alle ~130 vordefinierten Ticker.  
**Bitte warte — das dauert ca. 10–20 Minuten.** Du siehst laufende Ausgaben wie `ADS.DE`, `SAP.DE` usw.

Einige Ticker können mit `404 Not Found` fehlschlagen — das ist normal, Yahoo Finance kennt nicht jeden Ticker. Der Rest wird trotzdem geladen.

Am Ende erscheint wieder:
```
===========================================
 Installation abgeschlossen!
===========================================
```

---

## Schritt 6 — App starten

Doppelklicke auf die Datei **`start.bat`** im Trading-Ordner.

Ein schwarzes Fenster öffnet sich kurz, danach startet automatisch dein Browser mit der Trading-App.

> Falls der Browser nicht automatisch öffnet: Gib manuell `http://localhost:8080` in die Adressleiste ein.

---

## App beenden

Schließe das schwarze PowerShell/Terminal-Fenster, das beim Start geöffnet wurde.

---

## Übersicht der Installationsschritte

| Schritt | Befehl | Pflicht? | Dauer |
|---|---|---|---|
| Umgebung + Config anlegen | `.\install.ps1` | ✅ Ja | ~5 Min |
| Ticker-Stammdaten laden | `.\install.ps1 -InitInfo` | ✅ Ja | ~20 Min |
| Historische Kursdaten laden | `.\install.ps1 -Init` | Optional | Stunden |

---

## Häufige Probleme

### „python wird nicht erkannt" oder „py wird nicht erkannt"
Python wurde ohne PATH-Eintrag installiert. Lösung:
1. Python deinstallieren (Windows-Einstellungen → Apps)
2. Schritt 1 wiederholen — diesmal **unbedingt** das Häkchen „Add python.exe to PATH" setzen

### „running scripts is disabled on this system"
Du hast Schritt 3.3 übersprungen. Führe zuerst diesen Befehl aus:
```
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process
```
Danach nochmal `.\install.ps1`

### Die App öffnet sich, aber ich komme nicht rein
Du hast Benutzername und Passwort aus Schritt 4 verwendet? Groß-/Kleinschreibung beachten.

### „Port already in use" oder die App startet nicht
Ein anderes Programm blockiert Port 8501. Starte den PC neu und versuche es erneut.

---

## Ab dem nächsten Mal

Für jeden weiteren Start reicht ein Doppelklick auf **`start.bat`**.  
Die Installation (Schritte 1–5) muss nur einmal gemacht werden.

---

## Linux-Installation

### Was du brauchst

- Ubuntu 20.04+ / Debian 11+ / Fedora 36+ oder eine vergleichbare Distribution
- Internetverbindung (zwingend erforderlich — die App lädt Kursdaten von Yahoo Finance)
- Ca. 30–60 Minuten Zeit (davon ~20 Min. für den Daten-Download)

---

### Schritt 1 — Python installieren

**Ubuntu / Debian:**

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git
```

**Fedora / RHEL:**

```bash
sudo dnf install -y python3.11 git
```

Prüfen, ob die Installation geklappt hat:

```bash
python3.11 --version
```

Es sollte `Python 3.11.x` erscheinen. Python 3.10, 3.12 und 3.13 funktionieren ebenfalls.

---

### Schritt 2 — Code herunterladen

```bash
git clone https://github.com/online-junkie/trading-tools
cd trading-tools/Trading
```

Alternativ: ZIP von GitHub herunterladen und entpacken, dann in den `Trading`-Unterordner wechseln (dort liegt `asset_analyzer.py`).

---

### Schritt 3 — Installation ausführen

Mach das Script erst ausführbar, dann starte es:

```bash
chmod +x install.sh
./install.sh
```

Das Script läuft automatisch durch und gibt laufende Meldungen aus.  
**Bitte warte, das kann einige Minuten dauern.**

---

### Schritt 4 — Admin-Benutzer anlegen

Das Script fragt dich nach einem Benutzernamen und Passwort:

```
Admin-Benutzername (Standard: admin):
```
→ Enter drücken um „admin" zu übernehmen, oder einen eigenen Namen eingeben

```
E-Mail-Adresse (Standard: admin@localhost):
```
→ Enter drücken oder eigene E-Mail eingeben

```
Passwort:
Passwort bestätigen:
```
→ Passwort eingeben (mindestens 8 Zeichen) — die Eingabe wird **nicht angezeigt**, das ist normal

Am Ende erscheint:

```
===========================================
 Installation abgeschlossen!
===========================================
```

---

### Schritt 5 — Daten laden (Pflicht!)

> ⚠️ **Ohne diesen Schritt zeigt die App keine Daten an.**

```bash
./install.sh --init-info
```

Das Script lädt Metadaten (Name, Sektor, Branche) für alle ~130 vordefinierten Ticker von Yahoo Finance.  
**Bitte warte — das dauert ca. 10–20 Minuten.** Du siehst laufende Ausgaben wie `ADS.DE`, `SAP.DE` usw.

Einige Ticker können mit `404 Not Found` fehlschlagen — das ist normal. Der Rest wird trotzdem geladen.

---

### Schritt 6 — App starten

```bash
.venv/bin/streamlit run asset_analyzer.py
```

Die App öffnet sich automatisch im Browser unter `http://localhost:8501`.

> Falls der Browser nicht öffnet: Adresse manuell in die Adressleiste eingeben.

---

### App beenden

`Strg + C` im Terminal-Fenster drücken.

---

### Übersicht der Installationsschritte (Linux)

| Schritt | Befehl | Pflicht? | Dauer |
|---|---|---|---|
| Umgebung + Config anlegen | `./install.sh` | ✅ Ja | ~5 Min |
| Ticker-Stammdaten laden | `./install.sh --init-info` | ✅ Ja | ~20 Min |
| Historische Kursdaten laden | `./install.sh --init` | Optional | Stunden |

---

### Häufige Probleme (Linux)

#### „python3.11: command not found"

Python ist nicht installiert oder heißt anders auf dem System:

```bash
# Welche Python-Version ist verfügbar?
python3 --version

# Falls 3.10 oder neuer: direkt nutzen
python3 -m venv .venv
```

Das `install.sh` findet automatisch die neueste verfügbare Version (3.10–3.13).

#### TA-Lib lässt sich nicht installieren

TA-Lib benötigt unter Linux eine C-Bibliothek:

```bash
# Ubuntu/Debian:
sudo apt install libta-lib-dev

# Fedora/RHEL:
sudo dnf install ta-lib-devel
```

Danach `./install.sh` erneut ausführen. Die App läuft auch ohne TA-Lib; einige Indikatoren sind dann nicht verfügbar.

#### „Port already in use"

Ein anderer Streamlit-Prozess läuft noch. Beenden und neu starten:

```bash
pkill -f streamlit
.venv/bin/streamlit run asset_analyzer.py
```

Oder einen anderen Port verwenden:

```bash
.venv/bin/streamlit run asset_analyzer.py --server.port 8502
```

---

### Ab dem nächsten Mal (Linux)

```bash
cd trading-tools/Trading
.venv/bin/streamlit run asset_analyzer.py
```

Die Installation muss nur einmal gemacht werden.
