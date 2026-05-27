#Requires -Version 7
<#
.SYNOPSIS
    Trading-App Installer

.DESCRIPTION
    Phase 1 (immer):      Python prüfen · .venv anlegen · Pakete installieren · Verzeichnisse erstellen
    Phase 2 (immer):      config.yaml anlegen, falls noch nicht vorhanden (Benutzer wird abgefragt)
    Phase 3 (--Seed):     yf_tickers.db mit Beispiel-Tickern befüllen
    Phase 4 (--Init):     Asset-Metadaten + OHLC-Daten + Performance berechnen (dauert Stunden!)

.PARAMETER Seed
    Nur Ticker-DB befüllen (seed_db.py), keine Yahoo-Downloads.

.PARAMETER Init
    Vollständige DB-Initialisierung inkl. Yahoo-Downloads (braucht Internet, dauert lang).

.PARAMETER InitInfo
    Nur asset_info.db befüllen (Metadaten, kein OHLC-Download).

.EXAMPLE
    .\install.ps1                  # Umgebung + Config
    .\install.ps1 -Seed            # + Ticker-DB befüllen
    .\install.ps1 -InitInfo        # + Ticker-DB + Metadaten von Yahoo
    .\install.ps1 -Init            # Vollständige Initialisierung
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

# ---------------------------------------------------------------------------
# Verzeichnis-Prüfung — Installer muss im Trading-Ordner laufen
# ---------------------------------------------------------------------------

if (-not (Test-Path 'asset_analyzer.py')) {
    Write-Fail "Bitte den Installer aus dem Trading-Verzeichnis heraus starten."
    Write-Fail "  cd <Pfad zum Trading-Ordner>  dann  .\install.ps1"
    exit 1
}

$ROOT = $PWD.Path
Write-Host "`nTrading-App Installer" -ForegroundColor White
Write-Host "Arbeitsverzeichnis: $ROOT`n"

# ---------------------------------------------------------------------------
# Phase 1A: Python 3.11 finden
# ---------------------------------------------------------------------------

Write-Step "Phase 1A: Python 3.11 suchen"

$python = $null

# Bevorzuge py launcher (funktioniert auch wenn Python nicht im PATH ist)
if (Test-Command 'py') {
    $ver = py -3.11 --version 2>$null
    if ($LASTEXITCODE -eq 0) {
        $python = 'py -3.11'
        Write-Ok "py -3.11 gefunden: $ver"
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

# ---------------------------------------------------------------------------
# Phase 1B: Virtuelle Umgebung
# ---------------------------------------------------------------------------

Write-Step "Phase 1B: Virtuelle Umgebung"

if (Test-Path '.venv\Scripts\python.exe') {
    Write-Ok ".venv bereits vorhanden — überspringe Erstellung"
} else {
    Write-Info "Erstelle .venv …"
    # $python kann "py -3.11" (zwei Tokens) oder "python3.11" (ein Token) sein
    $py_parts = $python.Split(' ')
    & $py_parts[0] @($py_parts[1..($py_parts.Length-1)] + @('-m', 'venv', '.venv'))
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        Write-Fail "Erstellung der venv fehlgeschlagen."
        exit 1
    }
    Write-Ok ".venv erstellt"
}

$PY  = Join-Path $ROOT '.venv\Scripts\python.exe'
$PIP = Join-Path $ROOT '.venv\Scripts\pip.exe'

# ---------------------------------------------------------------------------
# Phase 1C: Pakete installieren
# ---------------------------------------------------------------------------

Write-Step "Phase 1C: Pakete installieren (requirements.txt)"

Write-Info "pip upgrade …"
& $PY -m pip install --upgrade pip --quiet

Write-Info "TA-Lib vorab prüfen …"
$talib_ok = & $PY -c "import talib" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Info "TA-Lib wird installiert (benötigt C-Library, kann etwas dauern) …"
    & $PIP install "TA-Lib>=0.6.0" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "TA-Lib-Installation fehlgeschlagen."
        Write-Warn "Alternativer Versuch mit vorkompiliertem Wheel:"
        & $PIP install "TA-Lib" --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "TA-Lib konnte nicht installiert werden."
            Write-Warn "Die App läuft ohne TA-Lib, einige Indikatoren sind dann nicht verfügbar."
        }
    }
}

Write-Info "Alle anderen Pakete installieren …"
& $PIP install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install fehlgeschlagen. Prüfe Netzwerkverbindung und requirements.txt."
    exit 1
}
Write-Ok "Pakete installiert"

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
# Phase 2: config.yaml anlegen
# ---------------------------------------------------------------------------

Write-Step "Phase 2: Authentifizierungs-Konfiguration"

if (Test-Path 'config.yaml') {
    Write-Info "config.yaml bereits vorhanden — überspringe"
} else {
    Write-Info "config.yaml fehlt — neuen Admin-Benutzer anlegen"
    Write-Host ""

    do {
        $username = Read-Host "  Admin-Benutzername (Standard: admin)"
        if ([string]::IsNullOrWhiteSpace($username)) { $username = 'admin' }
        $valid_user = $username -match '^[a-zA-Z0-9_]{3,32}$'
        if (-not $valid_user) { Write-Warn "Nur Buchstaben, Ziffern und _ erlaubt (3–32 Zeichen)." }
    } while (-not $valid_user)

    do {
        $email = Read-Host "  E-Mail-Adresse (Standard: admin@localhost)"
        if ([string]::IsNullOrWhiteSpace($email)) { $email = 'admin@localhost' }
    } while ($false)

    do {
        $pw1 = Read-Host "  Passwort" -AsSecureString
        $pw2 = Read-Host "  Passwort bestätigen" -AsSecureString
        $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw1))
        $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw2))
        if ($plain1 -ne $plain2) { Write-Warn "Passwörter stimmen nicht überein." }
        if ($plain1.Length -lt 8) { Write-Warn "Mindestens 8 Zeichen erforderlich." }
    } while ($plain1 -ne $plain2 -or $plain1.Length -lt 8)

    Write-Info "bcrypt-Hash wird berechnet …"
    $hash = & $PY -c @"
import bcrypt, sys
pw = sys.argv[1].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(12)).decode())
"@ $plain1

    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($hash)) {
        Write-Fail "Passwort-Hash konnte nicht erstellt werden."
        exit 1
    }

    # Zufälligen Cookie-Key erzeugen
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
pre-authorized:
  emails:
  - ${email}
cookie:
  expiry_days: 30
  key: ${cookie_key}
  name: trading_auth
"@

    Set-Content -Path 'config.yaml' -Value $yaml_content -Encoding UTF8
    Write-Ok "config.yaml erstellt (Benutzer: $username)"
    Write-Warn "WICHTIG: config.yaml enthält Passwort-Hash — niemals in Git committen!"
}

# ---------------------------------------------------------------------------
# Phase 3: Ticker-DB befüllen (--Seed / --Init / --InitInfo)
# ---------------------------------------------------------------------------

if ($Seed -or $Init -or $InitInfo) {
    Write-Step "Phase 3: Ticker-Datenbank befüllen (seed_db.py)"
    & $PY seed_db.py
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "seed_db.py fehlgeschlagen."
        exit 1
    }
    Write-Ok "yf_tickers.db befüllt"
} else {
    Write-Info "Ticker-DB nicht befüllt (kein -Seed / -Init / -InitInfo angegeben)"
    Write-Info "Zum Befüllen: .\install.ps1 -Seed"
}

# ---------------------------------------------------------------------------
# Phase 4: Yahoo-Downloads (--InitInfo oder --Init)
# ---------------------------------------------------------------------------

if ($InitInfo -or $Init) {
    Write-Step "Phase 4A: Asset-Metadaten laden (get_asset_info.py)"
    Write-Info "Lädt Ticker-Stammdaten von Yahoo Finance …"
    & $PY get_asset_info.py
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "get_asset_info.py mit Fehler beendet (einige Ticker wurden möglicherweise übersprungen)."
    } else {
        Write-Ok "asset_info.db befüllt"
    }
}

if ($Init) {
    Write-Step "Phase 4B: OHLC-Kursdaten laden (get_asset_data.py init)"
    Write-Warn "Dies lädt historische Kurse für alle Ticker — kann Stunden dauern!"
    $confirm = Read-Host "  Fortfahren? (j/N)"
    if ($confirm -match '^[jJyY]') {
        & $PY get_asset_data.py init
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "get_asset_data.py mit Fehler beendet."
        } else {
            Write-Ok "OHLC-Daten geladen"
        }
    } else {
        Write-Info "OHLC-Download übersprungen."
        Write-Info "Später nachholen: .venv\Scripts\python.exe get_asset_data.py init"
    }

    Write-Step "Phase 4C: Performance berechnen (asset_perf2.py init)"
    Write-Info "Berechnet Scores für alle Ticker …"
    & $PY asset_perf2.py init
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "asset_perf2.py mit Fehler beendet."
    } else {
        Write-Ok "Performance-DB befüllt"
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
    Write-Host " Ticker-DB noch leer. Nächster Schritt:" -ForegroundColor Yellow
    Write-Host "   .\install.ps1 -Seed       # nur Ticker eintragen"
    Write-Host "   .\install.ps1 -InitInfo   # Ticker + Metadaten"
    Write-Host "   .\install.ps1 -Init       # Vollständige DB-Initialisierung"
    Write-Host ""
}
