#Requires -Version 5.1
<#
.SYNOPSIS
    Trading-App Installer

.DESCRIPTION
    Phase 1 (immer):      Python pruefen - .venv anlegen - Pakete installieren - Verzeichnisse erstellen
    Phase 2 (immer):      config.yaml anlegen, falls noch nicht vorhanden (Benutzer wird abgefragt)
    Phase 3 (--Seed):     yf_tickers.db mit Beispiel-Tickern befuellen
    Phase 4 (--Init):     Asset-Metadaten + OHLC-Daten + Performance berechnen (dauert Stunden!)

.PARAMETER Seed
    Nur Ticker-DB befuellen (seed_db.py), keine Yahoo-Downloads.

.PARAMETER Init
    Vollstaendige DB-Initialisierung inkl. Yahoo-Downloads (braucht Internet, dauert lang).

.PARAMETER InitInfo
    Nur asset_info.db befuellen (Metadaten, kein OHLC-Download).

.EXAMPLE
    .\install.ps1                  # Umgebung + Config
    .\install.ps1 -Seed            # + Ticker-DB befuellen
    .\install.ps1 -InitInfo        # + Ticker-DB + Metadaten von Yahoo
    .\install.ps1 -Init            # Vollstaendige Initialisierung
#>
param(
    [switch]$Seed,
    [switch]$Init,
    [switch]$InitInfo
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

function Write-Step   { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan   }
function Write-Ok     { param($msg) Write-Host "    OK  $msg" -ForegroundColor Green  }
function Write-Info   { param($msg) Write-Host "    ... $msg" -ForegroundColor Gray   }
function Write-Warn   { param($msg) Write-Host "    WRN $msg" -ForegroundColor Yellow }
function Write-Fail   { param($msg) Write-Host "    ERR $msg" -ForegroundColor Red    }

function Test-Command {
    param([string]$cmd)
    return ($null -ne (Get-Command $cmd -ErrorAction SilentlyContinue))
}

# Rueckfrage vor dem (Neu-)Anlegen einer Datenbankdatei, die bereits existiert.
# Gibt $true (fortfahren) zurueck wenn die Datei fehlt oder der Nutzer bestaetigt,
# $false (ueberspringen) wenn die Datei existiert und der Nutzer ablehnt.
function Confirm-Overwrite {
    param([string]$desc, [string]$path)
    if (Test-Path $path) {
        Write-Warn "$desc existiert bereits: $path"
        $reply = Read-Host "  Trotzdem neu anlegen/aktualisieren? (j/N)"
        if ($reply -notmatch '^[jJyY]') {
            Write-Info "Uebersprungen -- bestehende Datei bleibt unveraendert."
            return $false
        }
    }
    return $true
}

# ---------------------------------------------------------------------------
# Verzeichnis-Pruefung -- Installer muss im Trading-Ordner laufen
# ---------------------------------------------------------------------------

if (-not (Test-Path 'asset_analyzer.py')) {
    Write-Fail "Bitte den Installer aus dem Trading-Verzeichnis heraus starten."
    Write-Fail "  cd <Pfad zum Trading-Ordner>  dann  .\install.ps1"
    exit 1
}

$ROOT = $PWD.Path
Write-Host "`nTrading-App Installer" -ForegroundColor White
Write-Host "Arbeitsverzeichnis: $ROOT`n"

# Datenbank-Verzeichnis -- respektiert TradingDB-Env analog zu tools.get_path()
$DB_DIR = if ($env:TradingDB) { $env:TradingDB } else { Join-Path $ROOT 'database' }

# ---------------------------------------------------------------------------
# Phase 1A: Python 3.11 finden
# ---------------------------------------------------------------------------

Write-Step "Phase 1A: Python 3.11 suchen"

$python = $null

# Bevorzuge py launcher (funktioniert auch wenn Python nicht im PATH ist)
if (Test-Command 'py') {
    $ver = py -3.11 --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        # Vollstaendigen Pfad zur python.exe auslesen -- vermeidet Probleme
        # beim Weiterreichen von "-3.11" als Argument in PS5
        $python_exe = (py -3.11 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $python_exe) {
            $python = $python_exe.Trim()
            Write-Ok "py -3.11 gefunden: $ver"
            Write-Info "Python-Pfad: $python"
        }
    }
}

if (-not $python) {
    foreach ($candidate in @('python3.11', 'python3', 'python')) {
        if (Test-Command $candidate) {
            $ver = & $candidate --version 2>$null
            if ($ver -match '3\.(11|12|13)') {
                $python = $candidate
                Write-Ok "$candidate gefunden: $ver"
                break
            }
        }
    }
}

if (-not $python) {
    Write-Fail "Python 3.11+ nicht gefunden."
    Write-Fail "Installieren: https://www.python.org/downloads/release/python-3119/"
    Write-Fail "Wichtig: 'Add python.exe to PATH' im Installer aktivieren."
    exit 1
}

# Sicherstellen dass 64-bit Python verwendet wird (32-bit hat keine vorkompilierten
# Wheels fuer scipy/scikit-learn und wuerde einen C-Compiler benoetigen)
$ErrorActionPreference = 'SilentlyContinue'
$bits = (& $python -c "import struct; print(struct.calcsize('P') * 8)" 2>&1)
$ErrorActionPreference = 'Stop'
if ($bits -ne '64') {
    Write-Fail "32-bit Python erkannt (gefunden: $python)."
    Write-Fail "Diese App benoetigt 64-bit Python 3.11."
    Write-Fail ""
    Write-Fail "Loesung:"
    Write-Fail "  1. Aktuelles Python deinstallieren (Windows-Einstellungen -> Apps)"
    Write-Fail "  2. 64-bit Version installieren:"
    Write-Fail "     https://www.python.org/downloads/release/python-3119/"
    Write-Fail "     --> Datei: python-3.11.9-amd64.exe  (nicht die win32-Version!)"
    Write-Fail "  3. Haekchen setzen: 'Add python.exe to PATH'"
    exit 1
}
Write-Ok "64-bit Python bestaetigt"

# ---------------------------------------------------------------------------
# Phase 1B: Virtuelle Umgebung
# ---------------------------------------------------------------------------

Write-Step "Phase 1B: Virtuelle Umgebung"

if (Test-Path '.venv\Scripts\python.exe') {
    Write-Ok ".venv bereits vorhanden -- ueberspringe Erstellung"
} else {
    Write-Info "Erstelle .venv ..."
    # $python ist immer ein einzelner Pfad (z.B. C:\Python311\python.exe)
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "venv-Erstellung fehlgeschlagen (exit code $LASTEXITCODE)."
        exit 1
    }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Fail "venv wurde nicht korrekt erstellt (.venv\Scripts\python.exe fehlt)."
        exit 1
    }
    Write-Ok ".venv erstellt"
}

$PY  = Join-Path $ROOT '.venv\Scripts\python.exe'
$PIP = Join-Path $ROOT '.venv\Scripts\pip.exe'

# ---------------------------------------------------------------------------
# Phase 1C: Pakete installieren
# ---------------------------------------------------------------------------

Write-Step "Phase 1C: Pakete installieren (requirements.txt + NLTK-Daten)"

Write-Info "pip upgrade ..."
& $PY -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip upgrade fehlgeschlagen -- fahre trotzdem fort."
}

Write-Info "TA-Lib vorab pruefen ..."
$ErrorActionPreference = 'SilentlyContinue'
& $PY -c "import talib" 2>&1 | Out-Null
$talib_exit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($talib_exit -ne 0) {
    Write-Info "TA-Lib wird installiert (kann etwas dauern) ..."
    & $PIP install "TA-Lib>=0.6.0" --prefer-binary --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "TA-Lib-Installation fehlgeschlagen."
        Write-Warn "Alternativer Versuch mit vorkompiliertem Wheel:"
        & $PIP install "TA-Lib" --prefer-binary --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "TA-Lib konnte nicht installiert werden."
            Write-Warn "Die App laeuft ohne TA-Lib, einige Indikatoren sind dann nicht verfuegbar."
        }
    }
}

Write-Info "Alle anderen Pakete installieren ..."
# --prefer-binary: nimmt vorkompilierte Wheels statt Quellcode-Build.
# Verhindert Fehler auf Systemen ohne C-Compiler (z.B. kein Visual Studio).
& $PIP install -r requirements.txt --prefer-binary --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install fehlgeschlagen."
    Write-Fail "Moegliche Ursachen:"
    Write-Fail "  1. Keine Internetverbindung"
    Write-Fail "  2. Paket benoetigt C-Compiler (Visual Studio Build Tools)"
    Write-Fail "     Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/"
    Write-Fail "  3. Python-Version nicht unterstuetzt -- empfohlen: Python 3.11"
    Write-Fail "     Download: https://www.python.org/downloads/release/python-3119/"
    exit 1
}
Write-Ok "Pakete installiert"

Write-Info "NLTK vader_lexicon herunterladen (Sentiment-Analyse) ..."
# SSL-Zertifikat-Pruefung deaktivieren -- noetig in eingeschraenkten Umgebungen
& $PY -c "import nltk, ssl; ssl._create_default_https_context = ssl._create_unverified_context; nltk.download('vader_lexicon', quiet=False)"
if ($LASTEXITCODE -ne 0) {
    Write-Warn "vader_lexicon konnte nicht heruntergeladen werden."
    Write-Warn "Die Sentiment-Analyse in der App wird deaktiviert."
} else {
    Write-Ok "vader_lexicon heruntergeladen"
}

# ---------------------------------------------------------------------------
# Phase 1D: Verzeichnisse anlegen
# ---------------------------------------------------------------------------

Write-Step "Phase 1D: Verzeichnisse anlegen"

foreach ($dir in @('database', 'logs')) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
        Write-Ok "$dir/ erstellt"
    } else {
        Write-Info "$dir/ bereits vorhanden"
    }
}

# ---------------------------------------------------------------------------
# Phase 1E: Fernet-Schluessel fuer ksplib generieren
# ---------------------------------------------------------------------------

Write-Step "Phase 1E: Fernet-Schluessel generieren (database/secret.key)"

if (Test-Path 'database\secret.key') {
    Write-Info "secret.key bereits vorhanden -- ueberspringe"
} else {
    Write-Info "Generiere neuen Fernet-Schluessel ..."
    & $PY -c "from tradinglib.ksplib import Ksp; Ksp()"
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "secret.key konnte nicht erstellt werden."
        Write-Warn "API-Zugangsdaten (Groq, Gemini, Pushover) koennen noch nicht gespeichert werden."
        Write-Warn "Der Schluessel wird automatisch beim ersten App-Start nachgeneriert."
    } else {
        Write-Ok "secret.key erstellt"
    }
}

# ---------------------------------------------------------------------------
# Phase 1F: Scheduler-Jobs seeden (Windows-Variante)
# ---------------------------------------------------------------------------
# Befuellt database/scheduler.db mit den Standard-Jobs, sofern die Tabelle noch
# leer ist (idempotent -- eine bestehende Job-Liste wird NIE ueberschrieben).
# Der Python-Pfad ergibt sich aus sys.executable (= diese .venv), die Skript-
# Pfade aus dem aktuellen Installationsverzeichnis -- keine hartcodierten Pfade.
# Bewusst NICHT geseedet: TradingDB-Env (nur Produktion), sowie die beiden
# broker-/order-ausfuehrenden Jobs STOPLOSS_TRAILING und EXECUTE_ORDER.

Write-Step "Phase 1F: Scheduler-Jobs seeden (database\scheduler.db)"

$seedPy = @'
import os, sys, sqlite3

ROOT = os.getcwd()
PY   = sys.executable                                    # = .venv-Python
DB   = os.path.join(ROOT, "database", "scheduler.db")
# Windows: Ruhezustand (nur diese Zeile unterscheidet sich von der Linux-Variante)
STANDBY = r'"C:\Windows\System32\shutdown.exe" /h'

os.makedirs(os.path.dirname(DB), exist_ok=True)
conn = sqlite3.connect(DB)
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, time TEXT, command TEXT, "
          "frequency TEXT, job_name TEXT, end_time TEXT, time_range TEXT, allowed_days TEXT, "
          "enabled INTEGER DEFAULT 1, last_run TEXT)")
c.execute("CREATE TABLE IF NOT EXISTS processes (id INTEGER PRIMARY KEY, task_id INTEGER, "
          "pid INTEGER, job_name TEXT)")
conn.commit()

c.execute("SELECT COUNT(*) FROM jobs")
if c.fetchone()[0] > 0:
    print("   scheduler.db enthaelt bereits Jobs -- Seed uebersprungen")
    sys.exit(0)

WD  = "monday,tuesday,wednesday,thursday,friday"          # Handelstage Mo-Fr
EU  = "^GDAXI,^MDAXI,^SDAXI,^SSMI,^IBEX,^FTSE,^STOXX50E"
# ^IXIC/^NYA sind hier bewusst nicht mehr enthalten: fuer beide gab es nie eine
# echte Mitgliederliste (15 bzw. 16 zufaellige Werte statt ~3000/~1900), sie
# wurden in Auswertungen aber wie ein Index behandelt. Breadth ueber den
# US-Markt deckt ^SPX/^NDX/^RUT ab.
US  = "^SPX,^DJI,^NDX"
# ^KS200 statt ^KS11: der KOSPI Composite umfasst rund 800 Notierungen ohne
# saubere oeffentliche Mitgliederliste, die 200 sind der handelbare Referenz-
# index. ^KS11 bleibt als Index-Ticker in der INDEX-Gruppe erhalten.
AS  = "^HSI,^N225,^KS200"

# (time, script, extra-args, frequency, job_name, end_time, time_range, allowed_days, enabled)
JOBS = [
    ("5",     "get_asset_data.py",     "1m:2d /group:"+EU,                    "minutes",        "YAHOO_M_DATA_MEMBER_EUROPE",  "",      "08:00-19:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+EU,            "hours",          "YAHOO_H_DATA_MEMBER_EUROPE",  "",      "08:00-19:00", WD, 1),
    ("3",     "get_asset_data.py",     "1m:2d /index",                        "minutes",        "YAHOO_M_DATA_INDEX",          "",      "08:00-19:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /index",                "hours",          "YAHOO_H_DATA_INDEX",          "",      "08:00-19:00", WD, 1),
    ("16:00", "asset_perf2.py",        "true /add_current /silent",           WD,               "ASSET_PERFORMACE",            "",      "",            "", 1),
    ("19:52", "asset_perf2.py",        "true /all /silent",                   "monday,wednesday,friday", "ASSET_PERFORMACE_ALL", "",     "",            "", 1),
    ("14:00", "get_asset_data.py",     "1m:7d 1d:2mo 60m:2mo 1mo:1y /all",    "saturday",       "YAHOO_PRICES",                "",      "",            "", 1),
    ("09:00", "get_asset_info.py",     "",                                    "sunday",         "ASSET_INFO",                  "",      "",            "", 1),
    ("08:00", "get_asset_data.py",     "60m:2d 1d:1mo /index_member",         WD,               "YAHOO_D_DATA_INDEX_MEMBER",   "",      "",            "", 1),
    ("09:00", "get_asset_data.py",     "60m:2d 1d:1mo /index",                WD,               "YAHOO_D_DATA_INDEX",          "",      "",            "", 1),
    ("10:00", "get_asset_data.py",     "1d:1mo /index_member",                WD,               "YAHOO_D_DATA_INDEX_MEMBER_2", "",      "",            "", 1),
    ("19:15", "get_asset_data.py",     "60m:2y 1d:2y /all",                   "saturday",       "YAHOO_ALL",                   "",      "",            "", 1),
    ("11:05", "get_asset_data.py",     "60m:2mo 1d:2mo 1m:7d /inverse",       WD,               "YAHOO_ALL_INVERSE_",          "19:45", "",            "", 1),
    ("22:30", None,                    "",                                    WD+",saturday",   "STANDBY",                     "22:45", "",            "", 0),
    ("22:00", "asset_perf2.py",        "true /add_current /silent",           WD,               "ASSET_PERFORMANCE_2",         "",      "",            "", 1),
    ("22:35", "recalc_correlation.py", "",                                    WD+",saturday",   "CORRELATION_INDEX",           "",      "",            "", 1),
    ("22:15", "get_asset_data.py",     "1d:1mo /select:'WHERE Ticker IN (\"TLT\",\"HYG\",\"LQD\",\"IEF\",\"IBB\",\"IHI\",\"IHF\",\"XPH\")'", WD, "YAHOO_D_DATA_FG_MACRO", "",      "",            "", 1),
    ("22:45", "log_fear_greed.py",     "/quiet",                              WD,               "FEAR_GREED_LOG",              "",      "",            "", 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:CRYPTO,COMMODITIES,METALS,CURRENCIES", "hours",   "YAHOO_H_DATA_GROUP",  "",      "",            "", 1),
    ("15",    "get_asset_data.py",     "1m:2d /group:CRYPTO,COMMODITIES,METALS,CURRENCIES",         "minutes", "YAHOO_M_DATA_GROUP",  "",      "",            "", 1),
    ("1",     "get_asset_data.py",     "1m:2d 60m:2d /group:ETP /worker:4",   "hours",          "YAHOO_H_DATA_ETP",            "",      "",            "", 1),
    ("22:00", "asset_perf2.py",        "/all /add_current /silent /group:ETP", WD+",saturday",  "ASSET_PERFORMANCE_ETP",       "",      "",            "", 1),
    ("10:00", "get_asset_data.py",     "1m:5d 1mo:5y 60m:2y 1d:5y /all /group:ETP /worker:4",       "saturday", "YAHOO_W_ETP",        "",      "",            "", 1),
    ("10:30", "warm_market_stress.py", "",                                    WD+",saturday,sunday", "WARM_MARKET_STRESS",     "",      "",            "", 1),
    ("10:35", "warm_rotation.py",      "",                                    WD+",saturday,sunday", "WARM_ROTATION",          "",      "",            "", 1),
    ("11:15", "check_freshness.py",    "/quiet /notify",                      WD+",saturday,sunday", "DATA_FRESHNESS",         "",      "",            "", 1),
    ("07:45", "stage_orders.py",      "/buys /stops",                        WD,               "ORDER_STAGING",               "",      "",            "", 0),
    ("5",     "get_asset_data.py",     "1m:2d /group:"+US,                    "minutes",        "YAHOO_M_DATA_MEMBER_AMERICAS", "",     "15:00-22:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+US,            "hours",          "YAHOO_H_DATA_MEMBER_AMERICAS", "",     "15:00-22:00", WD, 1),
    ("5",     "get_asset_data.py",     "1m:2d /group:"+AS,                    "minutes",        "YAHOO_M_DATA_MEMBER_ASIA",    "",      "22:00-08:00", WD, 1),
    ("1",     "get_asset_data.py",     "60m:2d 1d:1mo /group:"+AS,            "hours",          "YAHOO_H_DATA_MEMBER_ASIA",    "",      "22:00-08:00", WD, 1),
]

def build(script, extra):
    cmd = '"%s" "%s"' % (PY, os.path.join(ROOT, script))
    return cmd + (" " + extra if extra else "")

for (t, script, extra, freq, name, end, rng, days, en) in JOBS:
    command = STANDBY if script is None else build(script, extra)
    c.execute("INSERT INTO jobs (time, command, frequency, job_name, end_time, time_range, "
              "allowed_days, enabled, last_run) VALUES (?,?,?,?,?,?,?,?,NULL)",
              (t, command, freq, name, end, rng, days, en))
conn.commit()
conn.close()
print("   %d Scheduler-Jobs geseedet -> %s" % (len(JOBS), DB))
'@

$seedPy | & $PY -
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Scheduler-Seed fehlgeschlagen -- die Jobs koennen spaeter in der App (Tab 'Scheduler') angelegt werden."
} else {
    Write-Ok "Scheduler-Jobs bereit"
    Write-Info "Hinweis: Die order-ausfuehrenden Jobs STOPLOSS_TRAILING und EXECUTE_ORDER"
    Write-Info "werden bewusst NICHT automatisch angelegt. Bei Bedarf im Scheduler-Tab"
    Write-Info "manuell erganzen (Broker/Alpaca-Keys + gewuenschter --user noetig)."
}

# ---------------------------------------------------------------------------
# Phase 2: config.yaml anlegen
# ---------------------------------------------------------------------------

Write-Step "Phase 2: Authentifizierungs-Konfiguration"

if (Test-Path 'config.yaml') {
    Write-Info "config.yaml bereits vorhanden -- ueberspringe"
} else {
    Write-Info "config.yaml fehlt -- neuen Admin-Benutzer anlegen"
    Write-Host ""

    do {
        $username = Read-Host "  Admin-Benutzername (Standard: admin)"
        if ([string]::IsNullOrWhiteSpace($username)) { $username = 'admin' }
        $valid_user = $username -match '^[a-zA-Z0-9_]{3,32}$'
        if (-not $valid_user) { Write-Warn "Nur Buchstaben, Ziffern und _ erlaubt (3-32 Zeichen)." }
    } while (-not $valid_user)

    do {
        $email = Read-Host "  E-Mail-Adresse (Standard: admin@localhost)"
        if ([string]::IsNullOrWhiteSpace($email)) { $email = 'admin@localhost' }
    } while ($false)

    do {
        $pw1 = Read-Host "  Passwort" -AsSecureString
        $pw2 = Read-Host "  Passwort bestaetigen" -AsSecureString
        $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw1))
        $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw2))
        if ($plain1 -ne $plain2) { Write-Warn "Passwoerter stimmen nicht ueberein." }
        if ($plain1.Length -lt 8) { Write-Warn "Mindestens 8 Zeichen erforderlich." }
    } while ($plain1 -ne $plain2 -or $plain1.Length -lt 8)

    Write-Info "bcrypt-Hash wird berechnet ..."
    $hash = & $PY -c @"
import bcrypt, sys
pw = sys.argv[1].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(12)).decode())
"@ $plain1

    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($hash)) {
        Write-Fail "Passwort-Hash konnte nicht erstellt werden."
        exit 1
    }

    # Zufaelligen Cookie-Key erzeugen
    $cookie_key = & $PY -c "import secrets; print(secrets.token_hex(32))"

    $yaml_content = @"
credentials:
  usernames:
    ${username}:
      email: ${email}
      failed_login_attempts: 0
      logged_in: false
      name: ${username}
      password: ${hash}
      admin: true
pre-authorized:
  emails:
  - ${email}
cookie:
  expiry_days: 30
  key: ${cookie_key}
  name: trading_auth
"@

    # UTF-8 ohne BOM schreiben -- PS5 Set-Content -Encoding UTF8 fuegt BOM hinzu,
    # was Python's yaml.load() als ungueltige Zeichen vor "credentials:" liest.
    $utf8_no_bom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText((Join-Path $ROOT 'config.yaml'), $yaml_content, $utf8_no_bom)
    Write-Ok "config.yaml erstellt (Benutzer: $username)"
    Write-Warn "WICHTIG: config.yaml enthaelt Passwort-Hash -- niemals in Git committen!"
}

# ---------------------------------------------------------------------------
# Phase 3: Ticker-DB befuellen (--Seed / --Init / --InitInfo)
# ---------------------------------------------------------------------------

if ($Seed -or $Init -or $InitInfo) {
    Write-Step "Phase 3: Ticker-Datenbank befuellen (seed_db.py)"
    if (Confirm-Overwrite "yf_tickers.db" (Join-Path $DB_DIR 'yf_tickers.db')) {
        & $PY seed_db.py
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "seed_db.py fehlgeschlagen."
            exit 1
        }
        Write-Ok "yf_tickers.db befuellt"
    }
} else {
    Write-Info "Ticker-DB nicht befuellt (kein -Seed / -Init / -InitInfo angegeben)"
    Write-Info "Zum Befuellen: .\install.ps1 -Seed"
}

# ---------------------------------------------------------------------------
# Phase 4: Yahoo-Downloads (--InitInfo oder --Init)
# ---------------------------------------------------------------------------

if ($InitInfo -or $Init) {
    Write-Step "Phase 4A: Asset-Metadaten laden (get_asset_info.py)"
    if (Confirm-Overwrite "asset_info.db" (Join-Path $DB_DIR 'asset_info.db')) {
        Write-Info "Laedt Ticker-Stammdaten von Yahoo Finance ..."
        & $PY get_asset_info.py
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "get_asset_info.py mit Fehler beendet (einige Ticker wurden moeglicherweise uebersprungen)."
        } else {
            Write-Ok "asset_info.db befuellt"
        }
    }
}

if ($Init) {
    Write-Step "Phase 4B: OHLC-Kursdaten laden (get_asset_data.py)"
    $existingPriceDbs = @(Get-ChildItem -Path $DB_DIR -Filter 'yf_*.db' -ErrorAction SilentlyContinue |
                          Where-Object { $_.Name -ne 'yf_tickers.db' })
    if ($existingPriceDbs.Count -gt 0) {
        Write-Warn "$($existingPriceDbs.Count) vorhandene Kursdaten-Datei(en) (yf_<TICKER>.db) in $DB_DIR gefunden."
        Write-Warn "Der Download ergaenzt/aktualisiert bestehende Daten, ueberschreibt sie nicht pauschal."
    }
    Write-Warn "Dies laedt historische Kurse fuer alle Ticker -- kann Stunden dauern!"
    $confirm = Read-Host "  Fortfahren? (j/N)"
    if ($confirm -match '^[jJyY]') {
        # Nur die 4 Basis-Intervalle werden von Yahoo geholt:
        #   1m  -> min_data   (Intraday-Minuten, max. 7 Tage History bei Yahoo)
        #   60m -> h60_data   (Stunden, max. 60 Tage)
        #   1d  -> day_data   (Tage; asset_perf2 braucht moeglichst viel History)
        #   1mo -> month_data (Monate; asset_perf2 fragt 10y ab -> :max noetig)
        # 1wk wird NICHT separat geladen -- Wochendaten werden aus day_data aggregiert.
        # Alle anderen Intervalle (30m, 4h usw.) werden ebenfalls lokal aggregiert.

        # Schritt 1: Aktien aller Indizes laden (INDEX-Gruppe ausgeschlossen)
        Write-Info "Lade Aktien-Kursdaten (/index_member) ..."
        & $PY get_asset_data.py /index_member 1m:7d 60m:60d 1d:max 1mo:max
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "get_asset_data.py /index_member mit Fehler beendet."
        } else {
            Write-Ok "Aktien-OHLC geladen"
        }

        # Schritt 2: Index-Instrumente laden (^GDAXI, ^GSPC etc.)
        Write-Info "Lade Index-Kursdaten (/index) ..."
        & $PY get_asset_data.py /index 1m:7d 60m:60d 1d:max 1mo:max
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "get_asset_data.py /index mit Fehler beendet."
        } else {
            Write-Ok "Index-OHLC geladen (^GDAXI, ^GSPC, ...)"
        }
    } else {
        Write-Info "OHLC-Download uebersprungen."
        Write-Info "Spaeter nachholen:"
        Write-Info "  .venv\Scripts\python.exe get_asset_data.py /index_member 1m:7d 60m:60d 1d:max 1mo:max"
        Write-Info "  .venv\Scripts\python.exe get_asset_data.py /index 1m:7d 60m:60d 1d:max 1mo:max"
    }

    Write-Step "Phase 4C: Performance berechnen (asset_perf2.py init)"
    Write-Info "Berechnet Scores fuer alle Ticker ..."
    # /worker:2 -- begrenzt Parallelitaet um OOM in speicherarmen Umgebungen
    # (z.B. Windows Sandbox) zu vermeiden. Standard waere cpu_count/2-1.
    & $PY asset_perf2.py init /worker:2
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "asset_perf2.py mit Fehler beendet."
    } else {
        Write-Ok "Performance-DB befuellt"
    }
}

# ---------------------------------------------------------------------------
# Abschluss
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "===========================================" -ForegroundColor White
Write-Host " Installation abgeschlossen!" -ForegroundColor Green
Write-Host "===========================================" -ForegroundColor White
Write-Host ""
Write-Host " App starten:" -ForegroundColor Cyan
Write-Host "   .\start.bat"
Write-Host "   oder: .venv\Scripts\streamlit.exe run asset_analyzer.py"
Write-Host ""
if (-not ($Seed -or $Init -or $InitInfo)) {
    Write-Host ""
    Write-Host "  WICHTIG: Die App benoetigt Ticker-Stammdaten um zu funktionieren!" -ForegroundColor Red
    Write-Host "  Bitte jetzt ausfuehren:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    .\install.ps1 -InitInfo   <-- PFLICHT (laedt Daten von Yahoo Finance, ~20 Min)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Optional danach:" -ForegroundColor Gray
    Write-Host "    .\install.ps1 -Init       # + Kursdaten laden (dauert Stunden)"
    Write-Host ""
}
