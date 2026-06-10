@echo off
:: Wrapper for check_stoploss.py — use this path in the scheduler
:: Scheduler command field: C:\Users\kurtl\Claude\Trading\check_stoploss.bat
cd /d "%~dp0"
".venv\Scripts\python.exe" check_stoploss.py --atr-mult 2.5 --notify
