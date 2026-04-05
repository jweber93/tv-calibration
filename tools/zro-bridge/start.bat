@echo off
:: ZRO Bridge — Windows launcher
:: Run this on your Windows PC alongside ColourSpace ZRO.
:: The Calibration Helper web app will connect to this service on port 7070.

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

:: Create bridge.json from example if it doesn't exist
if not exist "%SCRIPT_DIR%bridge.json" (
    echo Creating bridge.json with default settings...
    copy "%SCRIPT_DIR%bridge.example.json" "%SCRIPT_DIR%bridge.json" >nul
    echo   Default: ColourSpace ZRO window, Space key triggers measurement.
    echo   Edit tools\zro-bridge\bridge.json if you need to change these.
    echo.
)

echo Starting ZRO Bridge...
echo.
echo  Bridge will listen on http://0.0.0.0:7070
echo  Enter this URL in the Calibration Helper settings: http://<this-pc-ip>:7070
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% "%SCRIPT_DIR%bridge.py" --config "%SCRIPT_DIR%bridge.json"

pause
