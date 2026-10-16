@echo off
setlocal
cd /d "%~dp0"
title AI Passport Voice (Agent Mode)
rem ===================================================================
rem AI Passport 语音 - 控制台调试模式(带窗口, 报错可见)
rem   日常使用请双击 start-agent-tray.cmd(托盘模式, 无窗口)
rem ===================================================================
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv 虚拟环境,请先运行 install.cmd 或联系配置。
  pause
  exit /b 1
)

rem === VBAN 音频串流由转发器自动拉起(读 voice-config.json 的 vban.target) ===

.venv\Scripts\python.exe -u hotkey_forwarder.py %*
if errorlevel 1 pause
