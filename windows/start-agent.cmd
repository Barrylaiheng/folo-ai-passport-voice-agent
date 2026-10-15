@echo off
setlocal
cd /d "%~dp0"
title AI Passport Voice (Agent Mode)
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv 虚拟环境,请先运行 install.cmd 或联系配置。
  pause
  exit /b 1
)
.venv\Scripts\python.exe -u hotkey_forwarder.py %*
if errorlevel 1 pause
