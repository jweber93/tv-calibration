@echo off
:: Dogegen Companion Agent — Windows launcher
:: Run this on your Windows PC alongside Dogegen.
:: A backend running anywhere on the LAN can connect to this service on port 7071.

setlocal

set SCRIPT_DIR=%~dp0

:: Use the Python from PATH, or specify a full path if needed.
:: e.g. set PYTHON=C:\Python311\python.exe
set PYTHON=python

:: Check Python is available
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.10+ from https://python.org and try again.
    pause
    exit /b 1
)

:: Install dependencies if not already present
echo Checking dependencies...
%PYTHON% -m pip install -q -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

:: Create agent.json from example if it doesn't exist
if not exist "%SCRIPT_DIR%agent.json" (
    echo Creating agent.json with default settings...
    copy "%SCRIPT_DIR%agent.example.json" "%SCRIPT_DIR%agent.json" >nul
    echo   Default: auto-detect Dogegen.exe, LAN-reachable on 0.0.0.0:7071.
    echo   Edit tools\dogegen-agent\agent.json if you need to change these.
    echo.
)

echo Starting Dogegen Companion Agent...
echo.
echo  Agent will listen on http://0.0.0.0:7071
echo  Enter this URL in the Calibration Helper settings: http://<this-pc-ip>:7071
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% "%SCRIPT_DIR%agent.py" --config "%SCRIPT_DIR%agent.json"

pause
