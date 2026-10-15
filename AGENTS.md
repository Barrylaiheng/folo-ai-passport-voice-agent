# AGENTS.md — AI 部署与开发手册

> 本文件写给 AI 编码助手（Codex / Claude Code / Cursor / WorkBuddy 等）。人类用户请看 [README.md](README.md) 与 [docs/QUICKSTART.zh-CN.md](docs/QUICKSTART.zh-CN.md)。
> 你的任务通常是：帮助用户在本机部署并验收"AI Passport 随身语音 Agent 模式"。请严格按下面的步骤执行，每步都要验证后再进入下一步，未验证的环节要如实告知用户。

## 项目一句话

FoloToy AI Passport 硬件按键 → WiFi UDP → 本机转发器 → 向目标 AI 应用（WorkBuddy / Trae / 其他）注入它自己的语音快捷键 → 应用自带的语音能力录音并转写进聊天输入框。本机不做 ASR，无云端依赖。

## 架构（windows/ 目录）

| 文件 | 职责 |
| --- | --- |
| `hotkey_forwarder.py` | 主程序：UDP 事件循环、音频桥（VB-Cable）、按键策略（Agent 模式/输入法模式）、焦点观察器、确定性窗口找回、CDP 调度 |
| `udp_transport.py` | 设备侧 UDP 协议：beacon 发现、ADPCM 音频流、事件收发、令牌校验 |
| `trae_cdp.py` | Trae（Chromium/Electron）CDP 直连：点麦克风/完成键。**只认 `vscode-file://vscode-app`/workbench 页面**，防止误连占用同一调试端口的其他程序 |
| `adpcm.py` | ADPCM 音频解码 |
| `diagnose_voice.py` | 自检：Python 版本、VB-CABLE 两端、UDP 端口、配置文件 |
| `voice-config.example.json` | 配置模板（部署时复制为 `voice-config.json`，该文件含个人配置，**不要提交到 git**） |

## 部署步骤（Windows）

```powershell
# 0) 前置检查：Python 3.10+ 且带 launcher
py -3 --version

# 1) 安装依赖（在 windows/ 目录下）
cd windows
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-hotkey.txt

# 2) 生成个人配置（已存在则跳过，不要覆盖用户已有配置）
if (!(Test-Path voice-config.json)) { Copy-Item voice-config.example.json voice-config.json }

# 3) 自检（必须全绿再继续）
.venv\Scripts\python.exe diagnose_voice.py
```

- **VB-CABLE**：若自检报 CABLE 缺失，引导用户从 https://vb-audio.com/Cable/ 下载安装并重启电脑（无法脚本化安装，需用户手动）。
- **设备**：提醒用户把 AI Passport 连到与电脑同一 2.4GHz WiFi，在设备上打开「AI语音」应用。USB 不传语音。
- **防火墙**：首次启动 Windows 可能弹窗放行 UDP 33333，选"允许"（仅专用网络）。

## 启动与验收

```powershell
# 启动（前台运行便于看日志；后台常驻用户可自行最小化）
cd windows
.venv\Scripts\python.exe -u hotkey_forwarder.py
```

验收顺序（必须让用户亲自操作，AI 只能看日志）：

1. 日志出现 `[udp] 发现设备 ...` 与 `[evt] 设备上线(device.hello)` = 链路通
2. 用户按住设备上键 → 日志 `[evt] 设备开始说话` + `[voice-key] 前台=... → 注入 ...` = 按键策略生效
3. 用户看到目标应用开始录音、松手后文字进输入框 = 端到端通
4. 下键回车发送

## 已知坑（重要，改代码前必读）

- **焦点不可信**：用户可能在开发任何东西，前台随时被抢。按键策略必须走"焦点观察器锚定的最近 Agent 窗口 → 按屏幕面积选主窗（避开悬浮球小窗）→ switch_to_window（等待重试核验）→ UIA 恢复输入框焦点"这条确定性链路，禁止只信 `GetForegroundWindow()`。
- **调试端口会冒名顶替**：其他 Chromium 程序（如 PyQt 应用写死 `--remote-debugging-port=9222`）会占用 TRAE 常用调试端口。CDP 连接前必须严格校验页面 URL，连不上就走键盘回退，绝不把任意 page 当目标，也绝不对错误页面执行 `Page.bringToFront`（会把冒名窗口拽到前台）。
- **TRAE 主窗口标题不含 "Trae"**（是工作区名如 `Agent [管理员]`），应用匹配必须同时支持 exe 进程名（`exe_contains`）。
- **切回窗口 ≠ 输入框聚焦**：TRAE 的 Alt+V 只认输入框聚焦；窗口级切换后必须尽力 UIA 恢复（Chromium DOM 焦点通常随窗口切换自动恢复，UIA 失败可接受）。
- **两处易错 ctypes**：`GetWindowThreadProcessId` 在 user32 不在 kernel32；`switch_to_window` 后立即查前台有竞态，需等待+重试。
- **注释里禁写 `/*`**：若用户把 windows/ 代码同步进 ESP-IDF 固件工程，xtensa-gcc 开 `-Werror=comment` 会因注释里的 `/*` 报错。

## 修改守则

- 改 `hotkey_forwarder.py` 的按键策略前，先读 `press_agent_voice_key` / `_recover_agent` / `_focus_watcher` 的 docstring——里面记录了每个坑的实测结论。
- 单元测试：`windows/tests/`，改动后用 `.venv\Scripts\python.exe -m pytest tests -x` 或逐文件 `python tests/test_xxx.py` 运行（无 pytest 时逐文件）。
- 不要引入云端 ASR / 录音上传；本项目红线是"语音不过云端、引导不给答案"。
- 提交信息用中文一句话说明行为变化；CHANGELOG.md 同步追加。
