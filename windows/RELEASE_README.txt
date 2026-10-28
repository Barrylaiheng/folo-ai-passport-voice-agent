AI Passport 随身语音 · Agent 模式  v0.4.0
========================================

把 FoloToy AI Passport（巴掌大的语音小钥匙）变成 WorkBuddy / Trae 等 AI 应用的无线语音键：
按住设备上键说话 → 松开自动转写进聊天框 → 短按下键发送。

【三步上手】
1. 装好 VB-CABLE 虚拟声卡（官网 vb-audio.com/Cable，装完重启一次电脑）。
2. 双击本目录的 AI-Passport-Voice-v0.4.0.exe —— 无窗口，右下角出现托盘图标即成功。
3. 设备打开「AI语音」→ 点进 AI 应用聊天框 → 按住上键说话 → 松开出字 → 下键发送。

【配置（可选，已带出厂默认，开箱即用）】
编辑同目录的 voice-config.json：
- apps：要接入的 AI 应用及其语音快捷键（默认 WorkBuddy / Trae CN）。
- vban.target：填另一台电脑 IP，把声音用 VBAN 推过去（留空 = 单机，零影响）。
- cross_trigger.target：填另一台电脑 IP，按键 UDP 直通到那台本地注入 Ctrl+D（留空 = 本机注入）。

【本版本 v0.4.0 要点】
- 跨机按键 UDP 直通：替代 DeskFlow，按键直发另一台电脑本地注入，不再"时灵时不灵"。
- 自研 VBAN 发送端：不再依赖外部 VBANServer.exe，打包版也能稳定推流。
- 系统托盘 v2：图标回到产品 VI，右键看运行状态，提示实时刷新。
- 跨机自动发现（P1）：B 机助手开机即广播，A 机托盘「重新扫描」点一下自动配对，免手填 IP。
完整更新见仓库 CHANGELOG.md；教程与排错见 README.md / docs/。

【跨机使用 · 两台电脑（可选）】
本 zip 自带 B 机助手，零 Python 依赖：
1. 在另一台电脑（B 机）解压本 zip，进 btrigger/ 子目录，右键 setup-bmachine.cmd → 以管理员运行
   （自动放行防火墙 + 登录自启）。
2. 回到你手边这台（A 机），右下角托盘 → 右键「重新扫描」→ 配对列表出现 B 机 → 点一下即配对。
   （老办法：编辑 voice-config.json 的 cross_trigger.target 填 B 机 IP 也行。）
详细见 docs/CROSS_MACHINE.zh-CN.md。

【排错】
托盘菜单 → 打开日志 看 forwarder.log。安全软件（尤其火绒）可能把全局键盘钩子进程当键盘记录器周期强杀，
请把本目录加入信任区 / 排除项。
