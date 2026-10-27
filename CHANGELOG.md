# 更新日志

## v0.4.0 · 2026-10-27（跨机按键 UDP 直通 + 自研 VBAN 发送端 + 托盘 v2）

这一轮把"两台电脑共用一台 Passport"的方案，从脆弱的 DeskFlow 跨屏转发换成稳定的 UDP 直通；并把 VBAN 推流从外部 exe 换成自研发送端，托盘升级到 v2：

- **跨机按键 UDP 直通（替代 DeskFlow）**：A 机转发器新增 `CrossTriggerSender`，按键事件直接 UDP 发往 B 机本地注入 `Ctrl+D`；B 机新增 `btrigger_daemon.py`（纯标准库：来源白名单 + 令牌校验 + 前台 WorkBuddy 校验 → SendInput）。彻底绕开 DeskFlow 光标脱节导致的"时灵时不灵"。配置 `cross_trigger.target` 留空 = 本机注入（单机零影响），填 B 机 IP 即切换；部署纪律写进代码注释（默认必须留空，只在 B 机助手部署好后才打开）。
- **自研 VBAN 发送端（替换有 bug 的 VBANServer.exe）**：转发器自带 VBAN 发送，不再依赖外部 `VBANServer.exe`；冻结（打包）版也能稳定推流（修复此前静默跳过推流的 bug）；新增 A/B 机对称监测脚本 `amon_forwarder.py` / `audio_monitor.py`。
- **系统托盘 v2**：图标回归产品 VI（黑底绿麦，内嵌 base64 兜底，不再依赖外部 ico 文件）；托盘 tooltip 实时刷新（设备在线 / VBAN / 已运行时长 / 模式）；右键菜单显示运行状态；发布包 ico 去重；修复打包版 VBAN 推流静默跳过。
- **可靠性 / 文档**：缺配置时回退出厂默认（开箱即用零配置）；补充"安全软件误杀"运维头号坑（火绒等周期强杀全局键盘钩子进程）与多机 DeskFlow 限制说明。

> 验证：A/B 机真机联调，B 机 `btrigger.log` 连续 20 对 start/stop 注入 `Ctrl+D` 零拦截零失败；VBAN 近 18h / 1189 万包 0 丢弃。

## v0.3.8 · 2026-10-16（系统托盘：双击即用、右下角看状态、无窗口常驻）

针对"日常使用 = 双击桌面图标就能用"的运营目标，把转发器从"黑窗口后台程序"升级为"托盘常驻服务"：

- **新增系统托盘**（`windows/tray_icon.py`，纯 ctypes / Shell_NotifyIconW，**零第三方依赖**）：右下角常驻小图标（passport-voice.ico），点开即见**运行状态**（设备在线/未连接 · 已运行时长），菜单 = 打开日志（forwarder.log）/ 退出。托盘不可用时自动降级为无托盘后台运行，不影响本体。
- **静默启动**：`start-agent-tray.cmd` 经 pythonw 无窗口启动；新增 `windows/create-desktop-shortcut.cmd` 一键生成桌面图标（直指 pythonw，双击零窗口闪现），"日常 = 双击桌面图标"开箱即成。pythonw 下 stdout/stderr 为 None 的问题已在 main() 入口兜底重定向。
- **VBAN 推流改为转发器自动管理**：`voice-config.json` 新增 `"vban": {"target": "B机IP", "port": 6980}`；启动时检测 `windows/vban/VBANServer/VBANServer.exe` 存在且未运行则自动拉起（CREATE_NO_WINDOW），target 留空或工具不存在自动跳过（纯单机零感知）。start-agent.cmd 里的手工 VBAN 启动段移除。修复：仅 CREATE_NO_WINDOW 时子进程继承启动者 stdout，启动者退出后句柄失效导致推流静默消失——补 stdin/stdout/stderr = DEVNULL 隔离（8 秒存活验证通过）。
- **新增命名互斥体单实例守卫**：修复"一秒内双击两次仍双开"（旧的端口检测存在竞态窗口，同秒启动时查表扑空）。
- **启动不再闪控制台窗口**：枚举本机 IP 的 `ipconfig`（udp_transport.py）与 VBAN 探测的 `tasklist` 均加 CREATE_NO_WINDOW——双击桌面图标后全程零画面变化（此前每次启动必闪一下黑窗）。
- **"退出"语义**：先停按键注入线程再退出，托盘图标同步移除；不再需要任务管理器找进程。
- 修复 ctypes 托盘两处 64 位陷阱：`Shell_NotifyIconW` 在 shell32 不在 user32；`DefWindowProcW` 必须显式声明 argtypes（wparam 为 64 位）。
- README 重写：新增"30 秒看懂""日常怎么用"，多机章节按作者真实两机场景重述（按键走 DeskFlow、声音走 VBAN、每台电脑装什么、两关验收法）。
- 全套 46 项单元测试通过。

## 0.3.0-agent.7 · 2026-10-16（适配 WorkBuddy 5.8.3 长按 Alt 手势）

实测发现 5.8.3 的语音手势从"点击切换"改成**长按 Alt**（按住说话、松开停录），单纯注入 Alt 点击无效。agent.6 的版本规则升级为 `alt:hold`：

- hold 型热键注入：**按下保持**（开录）/ **抬起释放**（停录），与"按住说话"节奏对齐；
- `finish()` 挂接长按键释放兜底（断链时不残留按住状态）；CDP 回退路径同步适配；
- 新增长按语义单元测试，全套 46 项通过。

## 0.3.0-agent.6 · 2026-10-16（版本感知热键：WorkBuddy 5.8.3 自动适配 Alt）

WorkBuddy 5.8.3 把「语音录音开关」默认热键从 Ctrl+D 改成单独 Alt（渲染层注册表 `{"id":"toggle-voice-recording","defaultWin":"Alt","voiceModifierOnly":true}`，实测注入 Alt 可正常开关录音），导致所有转发器用户升级即断链。本次让转发器**自动适配，用户零操作**：

- **版本感知解析**：注入前读前台 Agent 的 exe 文件版本（ctypes VerQueryValue，带缓存），命中内置规则表 `BUILTIN_VOICE_KEY_RULES` 则按版本选键 —— WorkBuddy ≥5.8.3 注 `alt`，旧版仍注 `ctrl+d`；其他应用（如 Trae `alt+v`）不受影响。
- **配置逃生舱**：应用内显式改过热键的用户，在 voice-config.json 对应条目加 `"voice_key_rules": []` 锁定 `voice_key`；也可用 `"voice_key_rules": [{"since":"x.y.z","key":"..."}]` 自定义版本规则。
- **两条注入路径都适配**：主路径（含焦点找回 —— 找回场景用目标 Agent 自己窗口的 exe 判版本，而非抢焦点者）与 CDP 键盘回退路径。
- 依据两机联调结论：兼容性由转发器做，不要求用户去改应用设置。
- 新增 17 项单元测试（版本解析/规则裁决/真机 WorkBuddy.exe 实测），全套 38 项通过。

## 0.3.0-agent.5 · 2026-10-16（DeskFlow 用户须知：按键跨机、音频不跨机）

依据两机 DeskFlow 场景的本机取证调研，在 README / QUICKSTART / TROUBLESHOOTING 补充"DeskFlow 用户须知"：

- **机制结论**：DeskFlow 只转发按键、不转发设备音频。光标在未装转发器的电脑上"能出字"，是两条链路拼出来的：① 按键链路真实跨机（DeskFlow 服务端 WH_KEYBOARD_LL 钩子无法区分真人按键与软件注入按键，一律捕获转发）；② 音频链路不跨机（设备语音 UDP 点对点只发给转发器所在电脑，光标所在电脑录音用的是它自己的默认麦克风，拾取房间声音转写）。
- **影响**：未装转发器的电脑识别质量明显打折，安静环境下容易被误判为"完全可用/未装也能用"。
- **文档补充**：机制说明 + 判别方法（静音该机本地麦克风再测 / 对比两台转写质量）。

## 0.3.0-agent.4 · 2026-10-16（回退多机自动切换，改推 DeskFlow 多机方案）

真机两台电脑实测后，回退 agent.3 的"多机自动切换"代码与文档（git revert `7524881`），并补充实测可行的多机方案：

- **回退原因（作者实测发现的设计缺陷）**：当两台电脑的光标**都停在各自的 AI 工具对话框里**时，两台都满足"前台是 AI 工具"，基于焦点/idle 的选举无法区分谁在真用（并列时按最小 IP 裁决，导致其中一台几乎永远抢不到设备）。要可靠裁决需引入全局键鼠输入钩子级别的信号——把简单问题复杂化，不值得。
- **结论**：代码回到 agent.2 状态（含同局域网冲突自检）；自动切换代码保留在 git 历史（`7524881`），日后若需要可复活为默认关闭的可选项。
- **改推实测方案**：多台电脑共用本功能时，用 [DeskFlow](https://github.com/deskflow-dev/deskflow)（Synergy 开源后继）把电脑用一套键鼠连成一体，转发器装在其中一台——打字跟随光标落到当前在用的那台电脑上，无设备冲突。这是作者的真实使用场景，README / QUICKSTART / TROUBLESHOOTING 已补充说明。
- 本机部署同步回退。

## 0.3.0-agent.2 · 2026-10-16（同局域网多机冲突自检）

针对"同一局域网两台电脑都用同一技术、冲突严重"的问题（情况二：一台设备被多台电脑共用）：

- 新增**冲突自检**（`udp_transport.py`）：转发器检测到同一局域网内有另一台电脑也在运行语音转发器、且本机未做单设备隔离（未配 `device_ip` / `pair_token`）时，在日志醒目标注"⚠ 另一台电脑也在运行"，并提示"同一台 AI Passport 同一时间只在一台运行"。限频 15s 不刷屏；已做单设备隔离的场景不误报。
- 优化连接超时提示：若长时间收不到设备，明确提示"可能被同一局域网内另一台电脑占用，请先在那台停止转发器"。
- README 新增"同局域网多台电脑（一台设备）"章节，讲清情况一/情况二的不同用法与唯一干净解（一台设备同一时间只跑一个转发器）。
- 文档下沉：同局域网冲突说明同步进 `docs/QUICKSTART.zh-CN.md`（第 7 节醒目提醒 + 9.3 轮换提醒）与 `docs/TROUBLESHOOTING.zh-CN.md`（现象表新增一行 + 第 6 节专章），让只看部署/排错文档的用户也能看到情况二用法。
- 新增 `windows/tests/test_udp_conflict.py`（4 项，覆盖提醒/限频/回环忽略/已隔离不提醒）。

说明：设备侧"每 ~1s 在电脑间乱跳"是 FoloToy 官方「随身语音」预编译固件的行为，本仓库不含可刷写固件，故未改动设备逻辑；冲突处理全部在电脑端做可见化与约束。设备端逻辑仍与上游 0.2.0 一致。

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
