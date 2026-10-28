# 跨机使用指南（v0.4.2 · 全自动跟随 DeskFlow 焦点）

> 场景：局域网内多台电脑共用一个 AI Passport。**人坐在哪台电脑前，设备语音就落到哪台机**——
> 由 DeskFlow 的屏幕焦点自动决定，全程零操作：不配对、不切换、不碰托盘。

---

## 1. 原理（三路 UDP + 焦点跟随）

| 通道 | 端口 | 方向 | 作用 |
|------|------|------|------|
| 触发直通 | UDP 33334 | A → 焦点机 | 焦点在客户机时，按键事件直发该机的 btrigger 助手本地注入 |
| 音频流 | UDP 6980 (VBAN) | A → 所有在线机 | 声音推到各机虚拟声卡（不触发录音，多推无副作用） |
| 在线发现 beacon | UDP 33335 | 各机 → A | 每台跑助手的电脑周期广播 `host/ip/port`，A 机自动维护在线表 |
| 焦点感知 | 本地日志 | — | A 机尾随 DeskFlow 日志的 `switch from X to Y` 行，实时知道人在哪台机前 |

- **焦点在 A 机本身** → A 机本地注入（铁律：本机永远可用）。
- **焦点在某台客户机** → 触发直通该机；该机不在线（30s 无 beacon）或 DeskFlow 未跑 → 兜底本机。
- **注入方式**：btrigger 用 `keybd_event`（VK 方式）——实测 WorkBuddy 录音键只认 VK 注入，
  扫描码 SendInput 不激活（v0.4.2 定案）。注入前还会自动找回并激活 Agent 窗口。

---

## 2. B 机（每台想被"跟着用"的电脑）：一次性部署

零 Python 依赖，发布包里是冻结好的独立 exe。

1. 解压发布 zip，进入 `btrigger/` 子目录，**右键 `setup-bmachine.cmd` → 以管理员身份运行**：
   - 放行防火墙入站 UDP 33334（规则名 "AI Passport btrigger"）；
   - 注册登录自启（重启后自动接管）；
   - 显示本机局域网 IP（仅核对信息用，**不需要填到任何地方**）。
2. 确认系统麦克风（设置 → 声音 → 输入）选 **CABLE Output (VB-Audio Virtual Cable)**。
   没装 VB-Cable 先从 vb-audio.com/Cable 安装。
3. 前台打开 WorkBuddy 或 Trae。完成——这台机从此自动可用。

> 想停掉：任务管理器结束 `AI-Passport-btrigger-v0.4.2.exe`；
> 彻底卸载再删 `shell:startup` 里的 `btrigger_daemon.bat`。
> 日志：exe 同目录 `btrigger.log`。

---

## 3. A 机：零配置

正常双击 `AI-Passport-Voice-v0.4.2.exe` 即可，不需要填任何 IP：

- 托盘菜单实时显示「跟随： 焦点在 X」与在线电脑列表（纯观测，没有需要点的东西）。
- `voice-config.json` 的 `cross_trigger.target` 与 `vban.target` 都**留空**即自动跟随；
  老用户若曾手填过 IP，清空即回到跟随模式。

---

## 4. 验证

1. 本机：光标进 WorkBuddy/Trae → 按设备上键 → 本机开始录音；下键发送。
2. 跨机：用 DeskFlow 切到 B 机 → 按设备上键 → B 机的 Agent 开始录音，下键发送。
   A 机日志同步出现 `焦点 <B机屏幕名> → 直通 <B机IP> (已发)`。

---

## 5. 排错

| 现象 | 排查 |
|------|------|
| 切到 B 机按键没反应 | ① B 机 `btrigger.exe` 是否在跑（看 `btrigger.log` 有无收到触发）；② 防火墙入站 UDP 33334（重跑 setup）；③ B 机前台是否 WorkBuddy/Trae；④ A/B 是否同网段 |
| 触发到了但 B 机不录音 | ① B 机是否装 VB-Cable 且输入设备选 CABLE Output；② B 机 Agent 的语音热键是否为 ctrl+d（WorkBuddy）/alt+v（Trae），不同则改 btrigger 启动参数 `--voice-key` |
| 托盘不显示某台在线机 | 那台机的 btrigger 没跑，或 beacon 被防火墙拦（出站广播默认放行） |
| DeskFlow 没跑时 | 自动兜底本机注入，跨机暂停，不影响单机使用 |

---

## 6. 给想自己编译的开发者

- B 机助手冻结：`windows/build/AI-Passport-btrigger-v0.4.2.spec`（纯标准库，`console=False`）。
- 发布包打包：`tools/make_release_zip.py`（自动带上 `btrigger/`、setup 脚本、两份 `_internal` 运行时）。
- beacon 协议：类型字节 `0x22` + JSON `{"role":"btrigger","host","ip","port"}` → `255.255.255.255:33335`。
- 焦点跟随实现：`hotkey_forwarder.py` 的 `_start_deskflow_watch` / `_route_target`；
  在线表 `_start_beacon_listener`；音频跟随 `_start_vban_follow`。
