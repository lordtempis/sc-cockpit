@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Star Citizen LIVE cockpit
set "project=%~dp0"
set "runtime=%LOCALAPPDATA%\SC-Cockpit"
if not exist "%runtime%\" mkdir "%runtime%"
if not exist "%runtime%\" goto no_runtime
cd /d "%runtime%" || goto no_runtime
set "log=%runtime%\startup.log"
echo LIVE cockpit startup
echo Log: "%log%"
call :run 3>&1 >"%log%" 2>&1
set "result=%errorlevel%"
if "%result%"=="0" goto stopped
echo.
echo Startup failed. The messages below are also saved in "%log%".
type "%log%"
goto finish
:stopped
echo The cockpit reader has stopped.
:finish
pause
exit /b %result%

:no_runtime
echo Could not open the Windows runtime folder "%runtime%".
pause
exit /b 1

:run
echo Started %date% %time%
set "game=%ProgramFiles%\Roberts Space Industries\StarCitizen\LIVE\Bin64\StarCitizen.exe"
if not exist "%game%" (
  echo Could not find the LIVE game at "%game%".
  exit /b 1
)
set "python=%runtime%\venv\Scripts\python.exe"
if exist "%python%" goto dependencies
echo Preparing Python on the Windows drive... >&3
py -3.12 -m venv "%runtime%\venv"
if errorlevel 1 py -3.11 -m venv "%runtime%\venv"
if errorlevel 1 exit /b 1
:dependencies
"%python%" -c "import grpc; from google.protobuf import message_factory; from google.protobuf.json_format import MessageToDict; import inspect; assert hasattr(message_factory, 'GetMessageClass') and 'always_print_fields_with_no_presence' in inspect.signature(MessageToDict).parameters"
if not errorlevel 1 goto launch
echo Installing dependencies. The first run can take a minute... >&3
"%python%" -m pip install -r "%project%requirements.txt"
if errorlevel 1 exit /b 1
:launch
echo Starting the reader. Cockpit address: http://127.0.0.1:8744 >&3
"%python%" -u "%project%cockpit.py" --game-exe "%game%" --port 8744 --open-browser
exit /b %errorlevel%
