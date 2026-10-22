@echo off
REM Read-only: connects to the running MT5 terminal and verifies demo status, instruments, clock and sessions.
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" -m metals_lab.cli check
pause
