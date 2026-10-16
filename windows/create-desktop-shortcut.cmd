@echo off
chcp 65001 >nul
title AI Passport 语音 - 创建桌面图标
echo.
echo  本脚本在桌面创建「AI Passport 语音」图标（一次即可）。
echo  之后每天双击该图标即可使用：无任何窗口，右下角托盘显示状态。
echo.
python make_desktop_shortcut.py
echo.
pause
