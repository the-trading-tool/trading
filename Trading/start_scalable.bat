@echo off
REM Scalable-Edition der Trading-App starten
REM Voraussetzung: install.ps1 + setup_scalable.ps1 wurden ausgefuehrt
REM Eigenes Datenverzeichnis (TradingDB) -> eigene config.yaml + eigene DBs

cd /d "%~dp0"

REM --- anpassbar: Datenverzeichnis und Port ---
set TRADING_EDITION=scalable
set "TradingDB=C:\Users\kurtl\scalable_data"
set SCALABLE_PORT=8081

if not exist ".venv\Scripts\activate.bat" (
    echo [FEHLER] .venv nicht gefunden. Bitte zuerst install.ps1 ausfuehren.
    pause
    exit /b 1
)

if not exist "%TradingDB%\config.yaml" (
    echo [FEHLER] config.yaml fehlt in "%TradingDB%".
    echo Bitte zuerst setup_scalable.ps1 ausfuehren:
    echo   powershell -ExecutionPolicy Bypass -File setup_scalable.ps1
    pause
    exit /b 1
)

echo Scalable-Edition  ^|  TradingDB=%TradingDB%  ^|  Port=%SCALABLE_PORT%
call .venv\Scripts\activate.bat
streamlit run asset_analyzer.py --server.port %SCALABLE_PORT%
