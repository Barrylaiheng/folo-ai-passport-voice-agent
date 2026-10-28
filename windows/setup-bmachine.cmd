@echo off
chcp 65001 >nul
title AI Passport · B 机一键部署
echo ============================================
echo  AI Passport 语音 · B 机触发助手部署
echo ============================================
echo.

REM --- 管理员权限检测(放行防火墙需要) ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] 需要管理员权限以放行防火墙 UDP 33334。
    echo     请右键本文件 → "以管理员身份运行"。
    pause
    exit /b 1
)

REM --- 定位 btrigger exe(与本 cmd 同目录, 文件名带版本号, 通配匹配) ---
set "HERE=%~dp0"
set "EXE="
for %%f in ("%HERE%AI-Passport-btrigger-*.exe") do set "EXE=%%f"
if not defined EXE (
    echo [!] 未找到 AI-Passport-btrigger-*.exe(应与本脚本同目录)。
    echo     请先解压完整的发布包。
    pause
    exit /b 1
)
echo 发现助手: %EXE%

REM --- 1) 放行 UDP 33334 入站(A 机跨机触发端口) ---
echo [1/3] 放行防火墙 UDP 33334 入站...
netsh advfirewall firewall delete rule name="AI Passport btrigger" >nul 2>&1
netsh advfirewall firewall add rule name="AI Passport btrigger" dir=in action=allow protocol=UDP localport=33334
if %errorLevel% equ 0 (echo       OK) else (echo       [!] 放行失败, 请手动检查)

REM --- 2) 注册登录自启(最小化运行, 开机自动接管 B 机注入) ---
echo [2/3] 注册登录自启...
"%EXE%" --install-startup
if %errorLevel% equ 0 (echo       OK) else (echo       [!] 自启注册失败)

REM --- 3) 提示后续(方案a: A 机跟随 DeskFlow 焦点自动发现, 免手填 IP) ---
echo [3/3] 本机局域网 IP(自动发现用, 仅核对信息):
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and ($_.IPAddress -like '192.168.*' -or $_.IPAddress -like '10.*' -or $_.IPAddress -like '172.16.*' -or $_.IPAddress -like '172.17.*' -or $_.IPAddress -like '172.18.*' -or $_.IPAddress -like '172.19.*' -or $_.IPAddress -like '172.2*' -or $_.IPAddress -like '172.3*')}).IPAddress" 2^>nul') do echo       - %%i

echo.
echo ============================================
echo  部署完成! B 机触发助手已就绪(开机自启 + 防火墙已放行)。
echo  下一步(零配置): 确认本机系统麦克风选 CABLE Output,
echo  前台打开 WorkBuddy/Trae 即可 —— A 机跟随 DeskFlow 焦点
echo  自动发现本机并直通, 无需在任何一台机上填 IP 或点配对。
echo ============================================
pause
