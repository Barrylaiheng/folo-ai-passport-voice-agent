@echo off
setlocal
cd /d "%~dp0"
rem ===================================================================
rem AI Passport 语音 - 托盘模式启动(推荐日常使用)
rem   - VBAN 音频串流由转发器自动拉起(配置 vban.target, 无需本脚本管理)
rem   - 本脚本只负责无窗口启动转发器; 右下角托盘 = 运行状态/日志/退出
rem   - 日志始终写入 forwarder.log
rem   调试排错请用 start-agent.cmd(带控制台, 报错能看到)
rem ===================================================================
if not exist ".venv\Scripts\pythonw.exe" (
  echo [错误] 未找到 .venv 虚拟环境,请先运行 install.cmd 或联系配置。
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -u hotkey_forwarder.py %*
exit /b 0
