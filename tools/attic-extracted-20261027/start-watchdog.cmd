@echo off
rem Launch the forwarder under the watchdog (auto-restart if killed by AV).
rem ASCII-only: CI enforces ASCII for .cmd files.
setlocal
cd /d "%~dp0"

set "PYW="
if exist ".venv\Scripts\pythonw.exe" set "PYW=.venv\Scripts\pythonw.exe"
if not defined PYW (
    for /f "delims=" %%P in ('where pythonw 2^>nul') do (
        if not defined PYW set "PYW=%%P"
    )
)
if not defined PYW (
    echo [watchdog] pythonw.exe not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

echo [watchdog] starting forwarder under watchdog via %PYW%
echo [watchdog] tip: right-click tray icon to quit; watchdog.log keeps restart history.
start "" "%PYW%" "%~dp0watchdog.py"
endlocal
