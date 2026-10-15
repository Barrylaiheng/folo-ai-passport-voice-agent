# 更新日志

## 0.3.0-agent.1 · 2026-10-15（基于上游 0.2.0-app.1 的二创首个版本）

新增"通用 Agent 模式"——语音快捷键不再只认输入法，可驱动任何配置了语音快捷键的 AI 应用：

- 新增 `apps` 多应用配置（`voice_key` / `title_contains` / `exe_contains` / `cdp_port` / `stop_on_release`），按前台应用自动注入对应快捷键；默认内置 WorkBuddy（Ctrl+D）与 Trae CN（Alt+V）。
- 新增**焦点观察器**（每 0.7s 记录用户最近所在的 Agent 窗口）与**确定性窗口找回**（锚定最近窗口 → 按屏幕面积选主窗避开悬浮球小窗 → 切前台核验 → UIA 恢复输入框焦点）。焦点被任何程序抢走时按键依然命中目标应用。
- 新增 Trae CDP 直连通道（可选加速）：点麦克风/完成键不依赖焦点；页面选择严格校验 `vscode-file://vscode-app`/workbench，防止误连占用同一调试端口的其他 Chromium 程序（如 PyQt 应用写死 9222）。
- 修复：键盘回退路径在"窗口切回但输入框失焦"场景下快捷键被忽略的问题（先 UIA 恢复焦点再注入）。
- 修复：`switch_to_window` 即时核验竞态导致的误报（等待+重试）。
- 修复：`GetWindowThreadProcessId` 误用 kernel32（应为 user32）导致的回调崩溃。
- DOWN 发送键同等确定性：前台非 Agent 时先找回再回车，避免回车打进无关窗口。
- 补齐依赖清单：pywinauto、websockets（上游 requirements 仅有 sounddevice）。
- 新增 AGENTS.md（AI 助手部署手册）、README 重写（双模式说明 + 接入新应用指南）。

适配验证：Windows 11 x64 + WorkBuddy + Trae CN 真机闭环（含"焦点被开发中程序抢占"的压力场景）。设备端逻辑与上游 0.2.0 完全一致，未改动。

## 0.2.0-app.1 · 2026-10-06（上游 manchunx7-bit/folo-ai-passport-voice）

- 将随身语音拆分为独立项目，仅公开本功能设备业务代码、Windows 配套程序、测试和教程。
- Windows 应用包不再携带整机固件、其他应用源码或旧整机历史；固件获取入口改为官网玩法页。
- 保留已验证的按住录音、松开释放、下键 Enter、残留状态对账和重复回车保护，不为本次拆分改变设备逻辑。
- 明确普通使用与开发者移植的区别，新增宿主接口说明及应用范围检查。

本版本是应用项目发布，不是新的整机固件。适配验证基于已测试的集成固件 0.2.0-rc.6；具体边界见 docs/VALIDATION.md。
