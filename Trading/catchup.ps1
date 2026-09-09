<#
.SYNOPSIS
    Holt den Tagesstand nach, wenn der Scheduler nicht laufen konnte (Reise,
    Ruhezustand). Gedacht fuer den Knopf "Jetzt starten" im Scheduler-Tab.

.DESCRIPTION
    Die naechtliche Kette besteht aus rund einem Dutzend Einzeljobs mit festen
    Uhrzeiten. Faellt die Maschine aus, laufen sie nicht nach - und sie einzeln
    per Knopf zu starten geht nicht: run_task() startet nur einen Unterprozess
    und kehrt sofort zurueck, zehn Klicks liefen also parallel und in falscher
    Reihenfolge. Dieses Skript macht dieselbe Kette SEQUENZIELL.

    Der eine inhaltliche Unterschied zu den Nachtjobs ist wichtig:

      Kurse   get_asset_data "1d:1mo" holt einen ganzen Monat. Eine Luecke von
              ein paar Tagen heilt sich damit von selbst.
      Scores  asset_perf2 rechnet im Standardlauf nur die letzten SECHS Tage
              (asset_perf2.py, start_of_year = heute - 6). Nach zwei Wochen
              Abwesenheit bliebe der Rest ein Loch in asset_simulation. Deshalb
              laeuft hier /fill: es ergaenzt genau die Tage, die fehlen.

    Ein einfaches Aneinanderreihen der bestehenden Job-Befehle waere also
    unvollstaendig gewesen.

    Fehler brechen den Lauf NICHT ab. Ein Aufhol-Lauf soll so weit kommen wie
    moeglich; welche Schritte gescheitert sind, steht in der Zusammenfassung und
    im Log. Der Exit-Code ist 1, sobald ein Schritt fehlschlug.

    ASCII only, absichtlich. Windows PowerShell 5.1 liest eine .ps1 ohne BOM als
    Windows-1252; das dritte Byte eines UTF-8-Gedankenstrichs wird dort zum
    schliessenden Anfuehrungszeichen und die Datei bricht Zeilen spaeter im
    Parser zusammen.

.PARAMETER Scope
    daily  (Vorgabe) Kurse der Index-Mitglieder und Indizes, die Makro-Ticker
           fuer Fear and Greed, danach /fill und die abgeleiteten Rechnungen.
    heavy  Die teuren Teile: /inverse (rund 3650 Einzeltitel ohne Indexbezug)
           und die ETP-Gruppe samt ihrer Simulation. Laeuft deutlich laenger und
           ist fuer den Tagesstand nicht noetig.
    all    Beides nacheinander.

.PARAMETER DryRun
    Zeigt die Schritte samt Befehl, fuehrt nichts aus.

.EXAMPLE
    .\catchup.ps1
    .\catchup.ps1 -Scope heavy
    .\catchup.ps1 -Scope all -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('daily', 'heavy', 'all')]
    [string]$Scope = 'daily',
    [switch]$DryRun
)

$AppDir = $PSScriptRoot
$Python = Join-Path $AppDir '.venv\Scripts\python.exe'
$LogDir = Join-Path $AppDir 'catchup_logs'

if (-not (Test-Path $Python)) { throw "Nicht gefunden: $Python" }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("catchup_{0}_{1}.log" -f $Scope, (Get-Date -Format 'yyyyMMdd-HHmm'))

# Schritt = Beschriftung, Python-Skript, Argumente. Reihenfolge ist bindend:
# erst Kurse, dann Scores, dann was auf den Scores aufbaut.
function New-Step([string]$Label, [string]$Script, [string[]]$ArgList) {
    # Bewusst NICHT $Args: das ist in PowerShell eine automatische Variable.
    [pscustomobject]@{ Label = $Label; Script = $Script; ArgList = $ArgList }
}

$daily = @(
    (New-Step 'Kurse: Index-Mitglieder'   'get_asset_data.py' @('60m:2d', '1d:1mo', '/index_member')),
    (New-Step 'Kurse: Indizes'            'get_asset_data.py' @('60m:2d', '1d:1mo', '/index')),
    # /tickers statt /select:'WHERE ...': der WHERE-Ausdruck enthaelt Leerzeichen
    # UND Anfuehrungszeichen und zerbricht deshalb je nach Shell.
    (New-Step 'Kurse: Makro (Fear&Greed)' 'get_asset_data.py' @('1d:1mo', '/tickers:TLT,HYG,LQD,IEF,IBB,IHI,IHF,XPH')),
    (New-Step 'Simulation: fehlende Tage' 'asset_perf2.py'    @('/fill', '/add_current')),
    (New-Step 'Marktstress vorwaermen'    'warm_market_stress.py' @()),
    (New-Step 'Rotation vorwaermen'       'warm_rotation.py'  @()),
    (New-Step 'Korrelationen'             'recalc_correlation.py' @()),
    (New-Step 'Fear and Greed protok.'    'log_fear_greed.py' @('/quiet'))
)

$heavy = @(
    (New-Step 'Kurse: Einzeltitel (/inverse)' 'get_asset_data.py' @('60m:2mo', '1d:2mo', '1m:7d', '/inverse')),
    (New-Step 'Simulation: Gesamtscheibe'     'asset_perf2.py'    @('/fill', '/all')),
    (New-Step 'Simulation: ETP-Gruppe'        'asset_perf2.py'    @('/fill', '/group:ETP', '/silent'))
)

# Die Frischepruefung steht immer am Ende - sie ist die Kontrolle, ob die
# Aufholung vollstaendig war, und nuetzt nur nach allen Schritten.
$freshness = (New-Step 'Kontrolle: Datenstand' 'check_freshness.py' @('/quiet'))

$steps = switch ($Scope) {
    'daily' { $daily + $freshness }
    'heavy' { $heavy + $freshness }
    'all'   { $daily + $heavy + $freshness }
}

Write-Host ""
Write-Host "Aufhol-Lauf ($Scope), $($steps.Count) Schritte" -ForegroundColor Cyan
Write-Host "Log: $LogFile" -ForegroundColor DarkGray
Write-Host ""

$results = @()
$stepNo = 0
foreach ($step in $steps) {
    $stepNo++
    $scriptPath = Join-Path $AppDir $step.Script
    $cmdText = "python $($step.Script) $($step.ArgList -join ' ')"
    Write-Host ("[{0}/{1}] {2}" -f $stepNo, $steps.Count, $step.Label) -ForegroundColor Cyan
    Write-Host "        $cmdText" -ForegroundColor DarkGray

    if (-not (Test-Path $scriptPath)) {
        Write-Host "        FEHLT: $scriptPath" -ForegroundColor Red
        $results += [pscustomobject]@{ Schritt = $step.Label; Status = 'fehlt'; Minuten = 0 }
        continue
    }
    if ($DryRun) {
        $results += [pscustomobject]@{ Schritt = $step.Label; Status = 'probelauf'; Minuten = 0 }
        continue
    }

    $t0 = Get-Date
    # ErrorActionPreference waehrend des Aufrufs auf Continue: PowerShell 5.1
    # verpackt stderr nativer Programme in ErrorRecords, und die Skripte loggen
    # nach stderr - mit 'Stop' braeche der Lauf an der ersten Logzeile ab.
    # Out-Host am Ende ist Pflicht, sonst landen die Logzeilen im Ausgabestrom
    # der Schleife und vermischen sich mit den Ergebnisobjekten.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        "=== $($step.Label) :: $cmdText ===" | Out-File -FilePath $LogFile -Append -Encoding UTF8
        & $Python $scriptPath @($step.ArgList) 2>&1 | Tee-Object -FilePath $LogFile -Append | Out-Host
        $code = $LASTEXITCODE
    } catch {
        Write-Host "        Ausnahme: $_" -ForegroundColor Red
        $code = 1
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    $status = if ($code -eq 0) { 'ok' } else { "exit $code" }
    $farbe = if ($code -eq 0) { 'Green' } else { 'Red' }
    Write-Host ("        {0} nach {1} min" -f $status, $mins) -ForegroundColor $farbe
    $results += [pscustomobject]@{ Schritt = $step.Label; Status = $status; Minuten = $mins }
}

Write-Host ""
Write-Host "Zusammenfassung" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-Host
$fehler = @($results | Where-Object { $_.Status -ne 'ok' -and $_.Status -ne 'probelauf' })
if ($fehler.Count -gt 0) {
    Write-Host ("{0} Schritt(e) fehlgeschlagen - Einzelheiten im Log." -f $fehler.Count) -ForegroundColor Red
    exit 1
}
Write-Host "Alle Schritte durchgelaufen." -ForegroundColor Green
exit 0
