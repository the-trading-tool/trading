<#
.SYNOPSIS
    Startet die Scalable-Edition der Trading-App.

.DESCRIPTION
    Setzt TRADING_EDITION=scalable (Edition-Flag) und TradingDB=<DataDir>
    (eigenes Daten- + Credential-Verzeichnis) und startet Streamlit auf einem
    eigenen Port, sodass die Scalable-Edition parallel zur vollen App laufen kann.

.PARAMETER DataDir
    Datenverzeichnis der Scalable-Edition (muss config.yaml enthalten -> setup_scalable.ps1).
    Default: C:\Users\kurtl\scalable_data

.PARAMETER Port
    Streamlit-Port. Default: 8081 (die volle App nutzt 8080).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File start_scalable.ps1
    powershell -ExecutionPolicy Bypass -File start_scalable.ps1 -DataDir D:\scalable -Port 8090
#>
param(
    [string]$DataDir = "C:\Users\kurtl\scalable_data",
    [int]$Port = 8081
)

$ErrorActionPreference = 'Stop'
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "[FEHLER] .venv nicht gefunden. Bitte zuerst install.ps1 ausfuehren." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $DataDir 'config.yaml'))) {
    Write-Host "[FEHLER] config.yaml fehlt in $DataDir." -ForegroundColor Red
    Write-Host "Bitte zuerst das Setup ausfuehren:" -ForegroundColor Yellow
    Write-Host "  .\setup_scalable.ps1 -DataDir `"$DataDir`"" -ForegroundColor Yellow
    exit 1
}

$env:TRADING_EDITION = 'scalable'
$env:TradingDB = $DataDir

Write-Host "Scalable-Edition" -ForegroundColor Cyan
Write-Host "  TRADING_EDITION = scalable"
Write-Host "  TradingDB       = $DataDir"
Write-Host "  Port            = $Port"
Write-Host ""

& .\.venv\Scripts\Activate.ps1
streamlit run asset_analyzer.py --server.port $Port
