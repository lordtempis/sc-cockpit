@echo off
setlocal EnableExtensions
title SC Cockpit door capture
set "runtime=%LOCALAPPDATA%\SC-Cockpit"
set "game=%ProgramFiles%\Roberts Space Industries\StarCitizen\LIVE\Bin64\StarCitizen.exe"
if not exist "%runtime%\venv\Scripts\python.exe" (
  echo Run Start-Live.cmd once to set up the reader, then run this capture.
  pause
  exit /b 1
)
"%runtime%\venv\Scripts\python.exe" -u "%~dp0capture_doors.py" "%game%" "%runtime%\captures"
set "result=%errorlevel%"
pause
exit /b %result%
