@echo off
setlocal
cd /d "%~dp0"
if exist "AI-Passport-Voice-agent.8.exe" (
  "AI-Passport-Voice-agent.8.exe" --diagnose
) else (
  if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" diagnose_voice.py
)
pause
