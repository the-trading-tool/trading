<#
.SYNOPSIS
    Einmal-Setup der Scalable-Edition: eigenes Datenverzeichnis + eigener Login.

.DESCRIPTION
    Die Scalable-Edition läuft aus derselben Codebasis wie die volle Trading-App,
    nutzt aber ein getrenntes Datenverzeichnis (Env-Var TradingDB). Da config.yaml
    ebenfalls über TradingDB aufgelöst wird, bekommt die Scalable-Edition damit
    automatisch eigene Credentials + eigenen Cookie-Key — kein gemeinsamer
    Nutzerkreis mit der vollen App.

    Dieses Skript legt das Datenverzeichnis an und erzeugt dort eine config.yaml
    mit einem Admin-Benutzer (bcrypt-Hash) und zufälligem Cookie-Key.

.PARAMETER DataDir
    Zielverzeichnis für die Scalable-Daten (config.yaml + alle DBs).
    Default: C:\Users\kurtl\scalable_data

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup_scalable.ps1
    powershell -ExecutionPolicy Bypass -File setup_scalable.ps1 -DataDir D:\scalable
#>
param(
    [string]$DataDir = "C:\Users\kurtl\scalable_data"
)

$ErrorActionPreference = 'Stop'
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

$PY = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path $PY)) {
    Write-Host "[FEHLER] .venv nicht gefunden. Bitte zuerst install.ps1 ausfuehren." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$cfg = Join-Path $DataDir 'config.yaml'

if (Test-Path $cfg) {
    Write-Host "config.yaml existiert bereits in $DataDir -- uebersprungen." -ForegroundColor Yellow
    Write-Host "Setup fertig. Start mit:  .\start_scalable.ps1 -DataDir `"$DataDir`"" -ForegroundColor Green
    exit 0
}

Write-Host "Neuen Admin-Benutzer fuer die Scalable-Edition anlegen ($DataDir)" -ForegroundColor Cyan
Write-Host ""

do {
    $username = Read-Host "  Admin-Benutzername (Standard: admin)"
    if ([string]::IsNullOrWhiteSpace($username)) { $username = 'admin' }
    $valid_user = $username -match '^[a-zA-Z0-9_]{3,32}$'
    if (-not $valid_user) { Write-Host "  Nur Buchstaben, Ziffern und _ (3-32 Zeichen)." -ForegroundColor Yellow }
} while (-not $valid_user)

$email = Read-Host "  E-Mail-Adresse (Standard: admin@localhost)"
if ([string]::IsNullOrWhiteSpace($email)) { $email = 'admin@localhost' }

do {
    $pw1 = Read-Host "  Passwort" -AsSecureString
    $pw2 = Read-Host "  Passwort bestaetigen" -AsSecureString
    $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw1))
    $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw2))
    if ($plain1 -ne $plain2) { Write-Host "  Passwoerter stimmen nicht ueberein." -ForegroundColor Yellow }
    if ($plain1.Length -lt 8) { Write-Host "  Mindestens 8 Zeichen erforderlich." -ForegroundColor Yellow }
} while ($plain1 -ne $plain2 -or $plain1.Length -lt 8)

Write-Host "bcrypt-Hash wird berechnet ..." -ForegroundColor Cyan
$hash = & $PY -c @"
import bcrypt, sys
pw = sys.argv[1].encode()
print(bcrypt.hashpw(pw, bcrypt.gensalt(12)).decode())
"@ $plain1

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($hash)) {
    Write-Host "[FEHLER] Passwort-Hash konnte nicht erstellt werden." -ForegroundColor Red
    exit 1
}

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
  name: scalable_auth
"@

# UTF-8 ohne BOM (PS5 Set-Content fuegt sonst BOM hinzu -> yaml.load bricht)
$utf8_no_bom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($cfg, $yaml_content, $utf8_no_bom)

Write-Host ""
Write-Host "config.yaml erstellt in $DataDir (Benutzer: $username)" -ForegroundColor Green
Write-Host "WICHTIG: config.yaml enthaelt den Passwort-Hash -- niemals in Git committen!" -ForegroundColor Yellow
Write-Host ""
Write-Host "Start der Scalable-Edition:" -ForegroundColor Green
Write-Host "  .\start_scalable.ps1 -DataDir `"$DataDir`"" -ForegroundColor Green
