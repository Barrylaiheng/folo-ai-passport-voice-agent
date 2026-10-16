@echo off
setlocal
cd /d "%~dp0"
title AI Passport Voice
if exist "AI-Passport-Voice-agent.8.exe" (
  "AI-Passport-Voice-agent.8.exe" %*
) else (
  if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first, or use the complete Windows release ZIP.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -u hotkey_forwarder.py %*
)
if errorlevel 1 pause
