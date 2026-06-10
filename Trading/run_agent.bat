@echo off
:: Wrapper for run_agent.py — use this path in the scheduler
:: Scheduler command field: C:\Users\kurtl\Claude\Trading\run_agent.bat
cd /d "%~dp0"
".venv\Scripts\python.exe" run_agent.py --notify
