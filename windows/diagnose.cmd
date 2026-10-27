@echo off
setlocal
cd /d "%~dp0"
if exist "AI-Passport-Voice-v0.4.0.exe" (
  "AI-Passport-Voice-v0.4.0.exe" --diagnose
) else (
  if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" diagnose_voice.py
)
pause
