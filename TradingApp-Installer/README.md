# Trading-App — Installations-Anleitung

Lokale Streamlit-Anwendung für technische Analyse, Portfolio-Management und Performance-Simulation.

---

## Voraussetzungen

| Anforderung | Details |
|---|---|
| **Betriebssystem** | Windows 10/11 (64-bit) |
| **Python** | 3.11 oder neuer — [Download](https://www.python.org/downloads/) |
| **PowerShell** | Version 7+ (pwsh) — [Download](https://github.com/PowerShell/PowerShell/releases) |
| **Internetverbindung** | Für die Paketinstallation und Yahoo-Finance-Downloads |

> **Python-Installationshinweis:** Im Python-Installer unbedingt **"Add python.exe to PATH"** aktivieren.

---

## Schnellstart

### Schritt 1 — Installer-Dateien in den App-Ordner kopieren

Die folgenden Dateien aus diesem Verzeichnis in den **Trading-App-Ordner** kopieren (dort wo `asset_analyzer.py` liegt):

```
install.ps1
requirements.txt
seed_db.py
start.bat
```

### Schritt 2 — PowerShell im App-Ordner öffnen

```powershell
cd C:\Pfad\zum\Trading-Ordner
```

### Schritt 3 — Installer ausführen

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Seed
```

Der Installer fragt interaktiv nach Benutzername und Passwort für den ersten Admin-Account.

### Schritt 4 — App starten

```
start.bat
```

Die App öffnet sich automatisch im Browser unter `http://localhost:8501`.

---

## Installer-Modi

| Aufruf | Was passiert |
|---|---|
| `.\install.ps1` | Umgebung + Pakete + `config.yaml`-Wizard |
| `.\install.ps1 -Seed` | + Ticker-Datenbank mit Beispieldaten befüllen |
| `.\install.ps1 -InitInfo` | + Metadaten aller Ticker von Yahoo Finance laden (~30 Min.) |
| `.\install.ps1 -Init` | Vollständige Initialisierung inkl. OHLC-Kursdaten (mehrere Stunden) |

Jeder Modus baut auf dem vorherigen auf. Empfohlene Reihenfolge für eine vollständige Installation:

```powershell
# 1. Umgebung aufbauen, Config anlegen, Ticker eintragen:
.\install.ps1 -Seed

# 2. Metadaten laden (Name, Sektor, Marktkapitalisierung …):
.\install.ps1 -InitInfo

# 3. Historische Kursdaten + Performance-Scores berechnen:
.\install.ps1 -Init
```

Schritte 2 und 3 können auch nachträglich jederzeit wiederholt werden, um die Daten zu aktualisieren.

---

## Was der Installer tut

### Phase 1 — Python-Umgebung

1. Sucht Python 3.11+ (py-Launcher → `python3.11` → `python`)
2. Legt eine virtuelle Umgebung `.venv` im App-Ordner an
3. Installiert alle Pakete aus `requirements.txt`
4. Erstellt die Verzeichnisse `database/` und `logs/`

### Phase 2 — Authentifizierung (`config.yaml`)

Legt beim ersten Aufruf interaktiv einen Admin-Benutzer an:

- Benutzername (Buchstaben, Ziffern, `_`, 3–32 Zeichen)
- E-Mail-Adresse
- Passwort (min. 8 Zeichen, wird als bcrypt-Hash gespeichert)

`config.yaml` wird **nicht** in Git versioniert (steht in `.gitignore`).

### Phase 3 — Ticker-Datenbank (`-Seed`)

Führt `seed_db.py` aus und befüllt `database/yf_tickers.db` mit 120 Beispiel-Tickern:

| Index | Anzahl | Beispiele |
|---|---|---|
| **GDAXI** (DAX 40) | 40 | ADS.DE, SAP.DE, SIE.DE, ALV.DE … |
| **MDAXI** (MDAX) | 19 | PUM.DE, NDX1.DE, TUI1.DE … |
| **SDAXI** (SDAX) | 14 | BVB.DE, AFX.DE, IONOS.DE … |
| **SPX** (S&P 500) | 40 | AAPL, MSFT, NVDA, TSLA … |
| **INDEX** | 7 | ^GDAXI, ^GSPC, ^VIX … |

> Index-Instrumente (`^GDAXI`, `^GSPC` …) landen im Bucket `INDEX` und werden von der Performance-Simulation automatisch ausgeschlossen.

### Phase 4 — Yahoo-Finance-Downloads (`-InitInfo` / `-Init`)

| Schritt | Skript | Dauer | Inhalt |
|---|---|---|---|
| 4A | `get_asset_info.py` | ~30 Min. | Stammdaten: Name, Sektor, KGV, Marktkapitalisierung … |
| 4B | `get_asset_data.py init` | 2–6 Std. | Historische OHLC-Kursdaten (Minuten, Stunden, Tage) |
| 4C | `asset_perf2.py init` | ~30 Min. | Technische Scores, Indikatoren, Sortino-Ratio |

Schritt 4B fragt vor dem Start nochmals nach Bestätigung, da er sehr lange dauert.

---

## Verzeichnisstruktur nach der Installation

```
Trading/
├── .venv/                  # Virtuelle Umgebung (nicht in Git)
├── database/               # SQLite-Datenbanken (nicht in Git)
│   ├── yf_tickers.db       # Ticker-Liste + Index-Zugehörigkeit
│   ├── asset_info.db       # Stammdaten (nach -InitInfo)
│   ├── yf_<TICKER>.db      # OHLC-Kursdaten je Ticker (nach -Init)
│   └── asset_simulation_.db # Performance-Scores (nach -Init)
├── config.yaml             # Auth-Config mit Passwort-Hash (nicht in Git)
├── logs/                   # Log-Dateien
├── asset_analyzer.py       # App-Einstiegspunkt
├── install.ps1             # Installer
├── requirements.txt        # Paketliste
├── seed_db.py              # Ticker-Seeder
└── start.bat               # App-Starter
```

---

## Bekannte Besonderheiten

### TA-Lib

TA-Lib benötigt eine native C-Bibliothek. Ab Version 0.6.0 liefert PyPI ein vorkompiliertes Windows-Wheel mit — `pip install TA-Lib` sollte direkt funktionieren. Falls nicht:

```powershell
# Manuell als Wheel installieren:
pip install --find-links https://github.com/ta-lib/ta-lib-python/releases TA-Lib
```

Die App startet auch ohne TA-Lib, einzelne Indikatoren sind dann nicht verfügbar.

### ExecutionPolicy

Falls PowerShell die Ausführung von Skripten blockiert:

```powershell
# Nur für diese Sitzung freigeben:
powershell -ExecutionPolicy Bypass -File install.ps1 -Seed

# Dauerhaft für den aktuellen Benutzer:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Umgebungsvariable `TradingDB`

Wenn die Datenbanken an einem anderen Ort liegen sollen (z. B. auf einer externen SSD):

```powershell
$env:TradingDB = 'D:\Trading\database'
.\install.ps1 -Seed
```

Der Installer und alle App-Skripte respektieren diese Variable automatisch.

### Weitere Benutzer anlegen

Nach der Erstinstallation können weitere Benutzer direkt in `config.yaml` eingetragen werden. Den bcrypt-Hash erzeugt man so:

```powershell
.venv\Scripts\python.exe -c "import bcrypt; print(bcrypt.hashpw(b'MeinPasswort', bcrypt.gensalt(12)).decode())"
```

---

## Datenbank aktualisieren (täglicher Betrieb)

```powershell
# Neue Tageskurse laden:
.venv\Scripts\python.exe get_asset_data.py true

# Performance-Scores neu berechnen:
.venv\Scripts\python.exe asset_perf2.py true

# Nur Scores neu berechnen (kein Download, sehr schnell):
.venv\Scripts\python.exe asset_perf2.py /rescore
```

Für automatische tägliche Aktualisierung steht der Scheduler-Daemon zur Verfügung:

```powershell
.venv\Scripts\python.exe schedserver.py
```

---

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| `python not found` | Python 3.11 installieren, „Add to PATH" aktivieren |
| `pip install` schlägt fehl | Internetverbindung prüfen; Proxy-Einstellungen in pip.ini setzen |
| App startet, aber kein Login-Dialog | `config.yaml` fehlt — `.\install.ps1` erneut ausführen |
| Leere Grafiken / keine Daten | `.\install.ps1 -InitInfo` oder `-Init` ausführen |
| `TA-Lib` nicht installierbar | Abschnitt "TA-Lib" oben beachten |
| Port 8501 belegt | `streamlit run asset_analyzer.py --server.port 8502` |
