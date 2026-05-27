@echo off
REM Trading-App starten
REM Voraussetzung: install.ps1 wurde mindestens einmal ausgeführt

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [FEHLER] .venv nicht gefunden.
    echo Bitte zuerst install.ps1 ausfuehren:
    echo   powershell -ExecutionPolicy Bypass -File install.ps1
    pause
    exit /b 1
)

if not exist "config.yaml" (
    echo [FEHLER] config.yaml fehlt.
    echo Bitte zuerst install.ps1 ausfuehren:
    echo   powershell -ExecutionPolicy Bypass -File install.ps1
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
streamlit run asset_analyzer.py
