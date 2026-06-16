# Scalable-Edition — Deployment

Die Scalable-Edition ist **dieselbe Codebasis** wie die volle Trading-App, nur
über zwei Umgebungsvariablen umgeschaltet. Bugfixes fließen automatisch in beide
Editionen; das „Upgrade" ist konzeptionell das Umschalten des Edition-Flags.

## Funktionsumfang

| | Scalable-Edition | volle App |
|---|---|---|
| Einstieg | **Own Transactions** (Scalable-CSV-Import) | Asset Viewer / Banner |
| Frei | Own Transactions, Asset Viewer, Market View, Sector Rotation, Lump Sum | alles |
| Upgrade (🔒) | Strategy Finder, Multi Strategies, Trading, Scoring/Signale, Admin | — |
| Daten | **On-Demand** je hochgeladenem Titel (Yahoo, FMP-Fallback) | Bulk-Pipeline |
| Login | ja (schützt die Depotdaten pro Nutzer) | ja |

## Die zwei Schalter

| Env-Var | Wert | Wirkung |
|---|---|---|
| `TRADING_EDITION` | `scalable` | Edition-Flag (gelesen in `tradinglib/app_edition.py`). Default `full` = bestehende App unverändert. |
| `TradingDB` | `C:\Users\kurtl\scalable_data` | Eigenes Verzeichnis für **alle** DBs **und** `config.yaml`. Wird automatisch angelegt. |

Wichtig: `config.yaml` wird über `Tools.get_path()` aufgelöst und folgt damit
ebenfalls `TradingDB`. Ein eigenes Datenverzeichnis bedeutet also **eigene
Credentials + eigener Cookie-Key** — kein gemeinsamer Nutzerkreis mit der vollen
App.

## Einrichtung (einmalig)

```powershell
# 1. Basis-Installation (falls noch nicht geschehen) — legt .venv + volle App an
powershell -ExecutionPolicy Bypass -File install.ps1

# 2. Scalable-Datenverzeichnis + eigenen Admin-Login anlegen
powershell -ExecutionPolicy Bypass -File setup_scalable.ps1 -DataDir "C:\Users\kurtl\scalable_data"
```

`setup_scalable.ps1` fragt Benutzername/Passwort ab, schreibt eine `config.yaml`
mit bcrypt-Hash und zufälligem Cookie-Key in das Datenverzeichnis.

## Starten

```powershell
# PowerShell
powershell -ExecutionPolicy Bypass -File start_scalable.ps1 -DataDir "C:\Users\kurtl\scalable_data" -Port 8081

# oder cmd (Pfad/Port oben in der Datei anpassen)
start_scalable.bat
```

Die volle App (`start.bat`, Port 8080) und die Scalable-Edition (Port 8081)
können parallel laufen — getrennte Ports, getrennte Datenverzeichnisse.

## Optional: Asset Viewer „Nach Markt auswählen" befüllen

Ein frisches `TradingDB`-Verzeichnis enthält noch keine Ticker-Universen, daher
ist die Markt-/Firmen-Auswahl im Asset Viewer zunächst leer. Der eigentliche
Consumer-Flow (CSV hochladen → analysieren) braucht das nicht — die hochgeladenen
Titel werden on-demand nachgeladen. Wer die Markt-Auswahl trotzdem befüllen will,
kopiert **nur die Universums-DB** (keine Nutzerdaten) aus der vollen App:

```powershell
Copy-Item "C:\Users\kurtl\Claude\Trading\database\yf_tickers.db" "C:\Users\kurtl\scalable_data\yf_tickers.db"
```

## FMP als ISIN-/Kursquelle (optional)

- **ISIN-Auflösung** nutzt FMP automatisch als Fallback, sobald ein Key hinterlegt
  ist (KSP-Eintrag `fmp`, Feld `password`). Ohne Key: nur lokaler Lookup + yfinance.
- **Kurse** kommen standardmäßig von Yahoo (`_app:data_provider` = `yahoo`). FMP nur,
  wenn explizit auf `fmp` gestellt. Empfehlung: bei `yahoo` belassen, FMP nur für die
  ISIN-Auflösung nutzen (dort ist der Fallback sauber).

## Vor dem öffentlichen Launch (Sicherheit)

- **Cookie-Key**: `setup_scalable.ps1` erzeugt einen zufälligen Key. Niemals den
  Repo-Default `trading_app_secret_key_change_me` verwenden (Auth-Bypass).
- **`eval()`-Themen** aus `CLAUDE.md` (Prio 1) gegenchecken — in einer öffentlich
  erreichbaren App kritischer als lokal (laut CLAUDE.md bereits via
  `ast.literal_eval` gefixt; vor Launch verifizieren).
- **Reverse Proxy / HTTPS**: hinter einem Proxy ggf. `baseUrlPath` in
  `.streamlit/config.toml` setzen (z. B. `/scalable`) und TLS terminieren.
- `config.yaml` (mit Passwort-Hash) **nie** committen — liegt im Datenverzeichnis,
  nicht im Repo.
