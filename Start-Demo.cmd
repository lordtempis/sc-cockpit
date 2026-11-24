@echo off
setlocal
cd /d "%LOCALAPPDATA%"
py -3.12 "%~dp0cockpit.py" --demo --port 8743 --open-browser
if errorlevel 1 pause
