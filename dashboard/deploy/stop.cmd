@echo off
cd /d "%~dp0\..\.."
python -B dashboard\deploy\local.py stop
if errorlevel 1 pause
