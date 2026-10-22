@echo off
REM Starts the backend (live loop in SHADOW mode unless you armed it) and opens the dashboard.
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\windows\setup.ps1 first.
  exit /b 1
)
start "" http://127.0.0.1:8765/
".venv\Scripts\python.exe" -m metals_lab.cli serve
