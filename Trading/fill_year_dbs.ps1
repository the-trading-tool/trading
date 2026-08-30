<#
.SYNOPSIS
    Fills the yearly asset_simulation_{year}.db slices: missing tickers, missing
    days, the atc columns that were never computed, and compacts the file.

.DESCRIPTION
    Three steps per year, because they cover different gaps:

      1. /backfill:atc /force - repairs atc_top_high / atc_bot_low on the rows that
                   already exist. They carry 0 (NaN written as 0.0 by the pdict
                   loop), and 0 silently breaks both formula sides:
                   `High >= atc_top_high` is always true, `Low <= atc_bot_low`
                   never is.
      2. /fill   - adds what is absent. collect_missing_dates() compares the local
                   day_data of every ^-index member against the simulation table,
                   so a ticker missing entirely yields all its trading days. Rows
                   that already exist are NOT recomputed - that is what makes this
                   cheaper than a full re-run, and why step 1 is needed at all.
      3. /vacuum - reclaims the space the inserts leave behind. Measured: the six
                   slices grow by ~3 GB (3.06 million rows at ~1 KB each), and
                   SQLite does not return freed pages on its own.

    The order matters. backfill_db() is single-threaded and walks EVERY ticker in
    the database; running it first keeps it on the 800-1000 tickers a year slice
    holds today. After /fill there would be ~3500, and the new ones do not need it
    anyway - the main run computes atc for them correctly.

    Years run sequentially - each year already parallelises across /worker
    processes, so running years side by side would only thrash the disk.

    The run is idempotent: /fill recomputes nothing that is already present, so a
    cancelled batch can simply be started again.

    ASCII only, deliberately. Windows PowerShell 5.1 reads a .ps1 without BOM as
    Windows-1252, where the third byte of a UTF-8 em dash becomes a closing curly
    quote - PowerShell accepts that as a string terminator and the parse collapses
    several lines later. Keep it to plain ASCII and the encoding stops mattering.

.PARAMETER Years
    Year slices to process. Default 2020..2025. The current year is deliberately
    excluded - it lives in asset_simulation_.db and is already complete.

.PARAMETER Worker
    Parallel worker processes. asset_perf2 caps itself at 2 on Windows (OOM
    guard); this passes /worker:N to override. Measured cost is ~25 s per ticker
    and year, so the worker count is the dominant lever. 6 is a reasonable
    default on a 12-core machine - raise it only if RAM allows.

.PARAMETER DryRun
    Report what is missing (via /fill /dry) and estimate the runtime. Reads only,
    writes nothing, skips the atc and vacuum steps.

.PARAMETER SkipAtc
    Skip step 1. Leaves the atc gaps of pre-existing rows in place.

.PARAMETER SkipVacuum
    Skip step 3. The files then keep the free pages until vacuumed by hand.

.EXAMPLE
    .\fill_year_dbs.ps1 -DryRun
    .\fill_year_dbs.ps1 -Worker 6
    .\fill_year_dbs.ps1 -Years 2024,2025 -Worker 8
#>
[CmdletBinding()]
param(
    [int[]]$Years = @(2020, 2021, 2022, 2023, 2024, 2025),
    [int]$Worker = 6,
    [switch]$DryRun,
    [switch]$SkipAtc,
    [switch]$SkipVacuum
)

$ErrorActionPreference = 'Stop'
$AppDir = $PSScriptRoot
$Python = Join-Path $AppDir '.venv\Scripts\python.exe'
$Script = Join-Path $AppDir 'asset_perf2.py'
$LogDir = Join-Path $AppDir 'fill_logs'

# Same rule asset_perf2 uses: the TradingDB env var wins, otherwise ./database.
$DbDir = if ($env:TradingDB) { $env:TradingDB } else { Join-Path $AppDir 'database' }

foreach ($p in @($Python, $Script)) {
    if (-not (Test-Path $p)) { throw "Nicht gefunden: $p" }
}
if (-not (Test-Path $DbDir)) { throw "Datenbank-Verzeichnis nicht gefunden: $DbDir" }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }

# Row/ticker counts and file size straight from the year DB, read-only.
function Get-SliceState([string]$dbFile) {
    $py = @'
import os, sqlite3, sys
p = sys.argv[1]
if not os.path.exists(p):
    print("0 0 0"); raise SystemExit
mb = os.path.getsize(p) / 1024 / 1024
try:
    c = sqlite3.connect("file:" + p + "?mode=ro", uri=True)
    if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='asset_simulation'").fetchone():
        print(f"0 0 {mb:.0f}"); raise SystemExit
    r = c.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM asset_simulation").fetchone()
    print(f"{r[0]} {r[1]} {mb:.0f}")
except sqlite3.Error:
    print(f"0 0 {mb:.0f}")
'@
    $tmp = Join-Path $env:TEMP "slice_state_$PID.py"
    Set-Content -Path $tmp -Value $py -Encoding UTF8
    try {
        $out = & $Python $tmp $dbFile 2>$null
        $last = ($out | Select-Object -Last 1)
        if (-not $last) { return [pscustomobject]@{ Rows = 0; Tickers = 0; MB = 0 } }
        $parts = "$last".Trim() -split '\s+'
        if ($parts.Count -lt 3) { return [pscustomobject]@{ Rows = 0; Tickers = 0; MB = 0 } }
        return [pscustomobject]@{ Rows = [int]$parts[0]; Tickers = [int]$parts[1]; MB = [int]$parts[2] }
    } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
}

function Invoke-Step([string]$label, [string[]]$argList, [string]$logFile) {
    Write-Host "  $label" -ForegroundColor Cyan
    Write-Host "    python asset_perf2.py $($argList -join ' ')" -ForegroundColor DarkGray
    $t0 = Get-Date
    # Zwei Fallen, beide nur in Windows PowerShell 5.1 sichtbar:
    #
    # 1. 5.1 verpackt stderr nativer Programme in ErrorRecords. asset_perf2 loggt
    #    nach stderr, also wuerde das globale ErrorActionPreference='Stop' den
    #    Lauf an der ERSTEN Logzeile abbrechen ("NativeCommandError"). Deshalb
    #    fuer den Aufruf auf 'Continue' zuruecknehmen.
    # 2. Out-Host am Ende ist Pflicht - ohne das landen die Logzeilen im
    #    Ausgabestrom der Funktion und vermischen sich mit dem Rueckgabeobjekt.
    #
    # Tee statt Out-Null, weil man bei einem Lauf ueber Stunden den Fortschritt
    # sehen will und nicht nur am Ende ein Ergebnis.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python $Script @argList 2>&1 | Tee-Object -FilePath $logFile -Append | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $mins = ((Get-Date) - $t0).TotalMinutes
    if ($code -ne 0) {
        Write-Host ("    fehlgeschlagen (Exit {0}) nach {1:N1} min. Log: {2}" -f $code, $mins, $logFile) -ForegroundColor Red
    } else {
        Write-Host ("    fertig in {0:N1} min" -f $mins) -ForegroundColor Green
    }
    return [pscustomobject]@{ ExitCode = $code; Minutes = $mins }
}

Write-Host ''
Write-Host '=== Jahres-DBs auffuellen ===' -ForegroundColor White
Write-Host "  App:        $AppDir"
Write-Host "  Datenbank:  $DbDir  $(if ($env:TradingDB) { '(aus TradingDB)' } else { '(Vorgabe)' })"
Write-Host "  Jahre:      $($Years -join ', ')"
Write-Host "  Worker:     $Worker$(if ($DryRun) { '   [DryRun - es wird nichts geschrieben]' })"
Write-Host "  PowerShell: $($PSVersionTable.PSVersion)"
Write-Host ''
if (-not $DryRun) {
    # Gemessen: ~25 s je Ticker und Jahr (5 Ticker, lokale Daten, kein Netz) und
    # 1759 (2020) bis 2644 (2025) unvollstaendige Ticker je Jahr.
    $est = [math]::Round(2100 * 25 / $Worker / 3600, 1)
    Write-Host "  Grobschaetzung: ~$est h pro Jahr bei $Worker Workern (~25 s je Ticker)." -ForegroundColor Yellow
    Write-Host '  Genaue Zahlen liefert -DryRun. Plane den Lauf ueber Nacht.' -ForegroundColor Yellow
    Write-Host '  Abbrechen ist unkritisch: /fill rechnet beim naechsten Start nur den Rest.' -ForegroundColor Yellow
    Write-Host ''
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$summary = @()
$runStart = Get-Date

foreach ($y in $Years) {
    $dbFile = Join-Path $DbDir "asset_simulation_$y.db"
    $log = Join-Path $LogDir "fill_${y}_$stamp.log"

    Write-Host "--- $y ---------------------------------------------" -ForegroundColor White
    $before = Get-SliceState $dbFile
    Write-Host ("  vorher: {0,9:N0} Zeilen, {1,5:N0} Ticker, {2,6:N0} MB" -f $before.Rows, $before.Tickers, $before.MB)

    if ($DryRun) {
        # /dry beendet asset_perf2 nach dem Bericht - nichts wird gerechnet.
        Invoke-Step 'Bestandsaufnahme (/fill /dry)' @("/fill", "/dry", "/year:$y") $log | Out-Null
        $summary += [pscustomobject]@{ Jahr = $y; Minuten = 0; Zeilen = 0; Ticker = 0; MB = 0; Status = 'dry' }
        Write-Host "  Bericht im Log: $log" -ForegroundColor DarkGray
        Write-Host ''
        continue
    }

    $mins = 0.0
    $status = 'ok'

    # Erst reparieren, dann ergaenzen - siehe Kopfkommentar: der Backfill ist
    # einspurig und laeuft ueber ALLE Ticker der DB. Vor dem /fill sind das
    # 800-1000, danach waeren es ~3500.
    if (-not $SkipAtc) {
        # /force, weil die Spalten existieren und mit 0 belegt sind - ohne /force
        # gilt "schon vorhanden" und der Backfill ueberspringt sie.
        $r1 = Invoke-Step 'Schritt 1/3: atc-Spalten reparieren (/backfill:atc /force)' `
            @("/backfill:atc", "/year:$y", "/force") $log
        $mins += $r1.Minutes
        if ($r1.ExitCode -ne 0) { $status = "atc Exit $($r1.ExitCode)" }
    } else {
        Write-Host '  Schritt 1/3 uebersprungen (-SkipAtc)' -ForegroundColor DarkGray
    }

    $r2 = Invoke-Step 'Schritt 2/3: fehlende Ticker und Tage (/fill)' `
        @("/fill", "/year:$y", "/worker:$Worker") $log
    $mins += $r2.Minutes
    if ($r2.ExitCode -ne 0) { $status = "fill Exit $($r2.ExitCode)" }

    # Vacuum zuletzt und nur, wenn ueberhaupt geschrieben wurde. asset_perf2
    # loest den Namen ueber das Muster auf und legt fuer den Lauf journal_mode
    # auf DELETE um - im WAL-Modus gaebe die Datei den Platz sonst nicht frei.
    if (-not $SkipVacuum -and $r2.ExitCode -eq 0) {
        $r3 = Invoke-Step 'Schritt 3/3: Datei kompaktieren (/vacuum)' `
            @("/vacuum:asset_simulation_$y") $log
        $mins += $r3.Minutes
        if ($r3.ExitCode -ne 0) { $status = "vacuum Exit $($r3.ExitCode)" }
    } elseif ($SkipVacuum) {
        Write-Host '  Schritt 3/3 uebersprungen (-SkipVacuum)' -ForegroundColor DarkGray
    }

    $after = Get-SliceState $dbFile
    Write-Host ("  nachher:{0,9:N0} Zeilen (+{1:N0}), {2,5:N0} Ticker (+{3:N0}), {4,6:N0} MB" -f `
        $after.Rows, ($after.Rows - $before.Rows), $after.Tickers, ($after.Tickers - $before.Tickers), $after.MB) -ForegroundColor Green
    Write-Host ''

    $summary += [pscustomobject]@{
        Jahr    = $y
        Minuten = [math]::Round($mins, 1)
        Zeilen  = $after.Rows - $before.Rows
        Ticker  = $after.Tickers - $before.Tickers
        MB      = $after.MB
        Status  = $status
    }
}

Write-Host '=== Zusammenfassung ===' -ForegroundColor White
$summary | Format-Table -AutoSize
Write-Host ("Gesamtdauer: {0:N1} h    Logs: {1}" -f ((Get-Date) - $runStart).TotalHours, $LogDir)
