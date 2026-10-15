@echo off
setlocal
cd /d "%~dp0"
title AI Passport Voice Agent - Install Python dependencies
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.10+ x64 from https://www.python.org/downloads/windows/
  echo Include the Python launcher, then run this file again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -r requirements-hotkey.txt
if errorlevel 1 goto fail
if not exist "voice-config.json" (
  copy /y "voice-config.example.json" "voice-config.json" >nul
  echo Created voice-config.json from template.
)
echo Installation complete. Install VB-CABLE separately and reboot.
echo Next: run diagnose.cmd to self-check, then start-agent.cmd.
pause
exit /b 0
:fail
echo Installation failed. Read the error above; see docs for troubleshooting.
pause
exit /b 1
