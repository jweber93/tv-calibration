@echo off
:: TV Calibration Windows Companion — launcher
:: Runs the Dogegen Companion Agent and the ZRO Bridge together, in one
:: process, on the Windows PC that owns the display + meter.

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

:: Create agent.json / bridge.json from their examples if missing
if not exist "%SCRIPT_DIR%agent.json" (
    echo Creating agent.json with default settings...
    copy "%SCRIPT_DIR%..\dogegen-agent\agent.example.json" "%SCRIPT_DIR%agent.json" >nul
    echo   Edit tools\windows-companion-tools\agent.json if you need to change these.
    echo.
)
if not exist "%SCRIPT_DIR%bridge.json" (
    echo Creating bridge.json with default settings...
    copy "%SCRIPT_DIR%..\zro-bridge\bridge.example.json" "%SCRIPT_DIR%bridge.json" >nul
    echo   Edit tools\windows-companion-tools\bridge.json if you need to change these.
    echo.
)

echo Starting TV Calibration Windows Companion...
echo.
echo  Dogegen Companion Agent will listen on http://0.0.0.0:7071
echo  ZRO Bridge will listen on              http://0.0.0.0:7070
echo  Enter these URLs in the backend's Dogegen Agent URL / ZRO Bridge URL settings.
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% "%SCRIPT_DIR%companion.py" --agent-config "%SCRIPT_DIR%agent.json" --bridge-config "%SCRIPT_DIR%bridge.json"

pause
