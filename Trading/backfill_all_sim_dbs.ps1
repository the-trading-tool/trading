<#
.SYNOPSIS
    Fuellt fehlende Indikator-Spalten/-Werte in allen asset_simulation_*.db
    (C:\Users\kurtl\Development\database) ueber asset_perf2.py /backfill auf.

.DESCRIPTION
    Nutzt ausschliesslich lokale OHLCV-Daten (yf_<TICKER>.db) -- kein
    Yahoo-Finance-Zugriff, kein Internet noetig.

    Pro DB (asset_simulation_.db, _2020..._2025.db, _all.db) werden zwei
    Backfill-Laeufe gemacht:

      1. "normale" Indikatoren (markov, rsi, adx, dema, hor, sup, relvol):
         ohne /force -- der eingebaute Skip-Check (erste Spalte IS NOT NULL)
         ist hier zuverlaessig und ueberspringt bereits befuellte Ticker.

      2. "force"-Indikatoren (heikin, macd, ewo, atc):
         mit /force -- diese Spalten wurden per ALTER TABLE ... DEFAULT 0
         nachtraeglich angelegt, der Skip-Check wuerde sie faelschlich als
         "schon befuellt" werten und alles uebergehen.

.PARAMETER Years
    Optionale Liste der zu verarbeitenden Jahre (Default: alle).
    '' steht fuer asset_simulation_.db (aktuelles Jahr ohne Suffix),
    'all' fuer asset_simulation_all.db.

    Beispiel: -Years 2024,2025

.EXAMPLE
    .\backfill_all_sim_dbs.ps1
    .\backfill_all_sim_dbs.ps1 -Years 2025,all
#>

param(
    [string[]]$Years = @('', '2020', '2021', '2022', '2023', '2024', '2025', 'all')
)

$ErrorActionPreference = 'Continue'

$tradingDir = $PSScriptRoot
$env:TradingDB = "C:\Users\kurtl\Development\database"
$python = Join-Path $tradingDir ".venv\Scripts\python.exe"
$script = Join-Path $tradingDir "asset_perf2.py"

$logDir = Join-Path $tradingDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Indikatoren mit zuverlaessigem Skip-Check -> ohne /force (schnell)
$normalIndicators = 'markov,rsi,adx,dema,hor,sup,relvol'

# Indikatoren mit per ALTER TABLE...DEFAULT 0 nachtraeglich angelegten Spalten
# -> Skip-Check waere falsch-positiv, daher /force
$forceIndicators = 'heikin,macd,ewo,atc'

function Invoke-Backfill {
    param(
        [string]$Indicators,
        [string[]]$ExtraArgs,
        [string]$Label
    )

    $timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $logFile = Join-Path $logDir "backfill_${Label}_${timestamp}.log"

    Write-Host "=== $Label : /backfill:$Indicators $($ExtraArgs -join ' ') ===" -ForegroundColor Cyan
    $allArgs = @("/backfill:$Indicators") + $ExtraArgs + @("/logfile:$logFile")
    & $python $script @allArgs
}

Push-Location $tradingDir
try {
    foreach ($year in $Years) {
        if ($year -eq 'all') {
            $label = 'asset_simulation_all'
            $extra = @('/all')
        }
        elseif ($year -eq '') {
            $label = 'asset_simulation_'
            $extra = @()
        }
        else {
            $label = "asset_simulation_$year"
            $extra = @("/year:$year")
        }

        Invoke-Backfill -Indicators $normalIndicators -ExtraArgs $extra -Label "${label}_normal"
        Invoke-Backfill -Indicators $forceIndicators -ExtraArgs ($extra + @('/force')) -Label "${label}_force"
    }
}
finally {
    Pop-Location
}

Write-Host "Fertig. Logs unter $logDir" -ForegroundColor Green
