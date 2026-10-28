# 跨机使用指南（A 机 + B 机）

> 适用版本：v0.4.0（含 P0 一键部署 + P1 自动发现）
> 场景：你有两台电脑，想把 AI Passport 的「语音键」从 A 机直接打到 B 机本地注入，
> 同时把声音用 VBAN 推到 B 机，省去 DeskFlow 那种「时灵时不灵」的光标搬运。

---

## 1. 原理（三路 UDP，互不耦合）

| 通道 | 端口 | 方向 | 作用 |
|------|------|------|------|
| 触发直通 | UDP 33334 | A → B | A 机按键 → B 机本地 `SendInput` 注入语音快捷键（Ctrl+D 之类） |
| 音频流 | UDP 6980 (VBAN) | A → B | A 机把「你说的话」推到 B 机虚拟声卡，B 机 Agent 录到的就是你 |
| 自动发现 beacon | UDP 33335 | B → A | B 机周期广播自己的 IP，A 机托盘自动列出可配对电脑 |

- **A 机 = 你手边操作 AI Passport 的电脑**，跑开源的 `AI-Passport-Voice-v0.4.0.exe`（即开即用，零配置）。
- **B 机 = 你想被远程注入语音键的电脑**（比如跑 WorkBuddy 的主力机），跑 `btrigger/` 里的 B 机助手。
- DeskFlow 只负责把鼠标光标搬过去；本方案按键和音频都不走 DeskFlow，因此不再受光标焦点漂移影响。

---

## 2. B 机：一键部署（零 Python 依赖）

B 机**不需要装 Python、不需要 AI agent 配置**。发布 zip 里已经冻结好了独立 exe。

1. 在 B 机解压 `AI-Passport-Voice-v0.4.0.zip`。
2. 进入 `btrigger/` 子目录，**右键 `setup-bmachine.cmd` → 以管理员身份运行**。
   - 放行防火墙入站 UDP 33334（"AI Passport btrigger" 规则）。
   - 把 btrigger 写进登录自启（重启后自动跑）。
   - 弹出 B 机局域网 IPv4，记下来备用（自动发现用不上，但手动配对时会看到）。
3. 完成后 btrigger 在后台运行（无窗口，日志在同目录 `btrigger.log`）。
   - 它每 2 秒向 `255.255.255.255:33335` 广播 beacon，携带 `host / ip / port`。

> 想停掉：任务管理器结束 `AI-Passport-btrigger-v0.4.0.exe`，或删掉
> `shell:startup` 里的 `AI-Passport-btrigger 自启.bat`。

---

## 3. A 机：两种配对方式

### 方式一 · 自动发现（推荐，P1）

A 机托盘默认就在监听 beacon，无需手填 IP：

1. 右下角托盘图标 → **右键 → 重新扫描**（清空旧列表，重新监听 33335）。
2. 几秒内托盘菜单的「配对列表」里会出现 `配对 → <B机名> (<B机IP>)`。
3. **点选它** → 自动完成：
   - 热改运行态 `cross.target`，立刻生效（不用重启 A 机 exe）；
   - 同时把 `cross_trigger.target` 写进 `voice-config.json` 持久化（下次启动沿用）。

> token 不会因此被覆盖：沿用 B 机「学习首个来源」安全模型（谁先打到 33334 就信任谁）。

### 方式二 · 手动填 IP（老办法，仍可用）

编辑 A 机同目录的 `voice-config.json`：

```json
{
  "cross_trigger": { "target": "192.168.110.127" }
}
```

- `target` 填 B 机局域网 IP（方式二里 setup 弹出的那个，或 B 机 `ipconfig` 查到的 IPv4）。
- 留空 = 本机注入（单机模式，零影响）。
- 改完重启 A 机 exe 生效。

---

## 4. 验证是否联通

A 机托盘菜单会实时显示三段状态：

- `设备: 在线 ✓` —— AI Passport 硬件已连上
- `模式: 跨机 → B(192.168.x.x)` —— 已配对到 B 机（显示 B 的 IP）
- `音频: VB-Cable 就绪` —— A 机音频链路正常

看到「模式: 跨机 → B(...)」即代表触发直通已生效。按住 A 机设备**上键**说话，
B 机对应 AI 应用应开始录音；松手出字，B 机**下键**发送。

---

## 5. 排错

| 现象 | 排查 |
|------|------|
| 托盘配对列表里看不到 B 机 | ① A/B 是否在**同一网段**（beacon 不过路由器）；② B 机 `btrigger.exe` 是否在跑；③ B 机防火墙是否放行了**出站**广播（一般默认允许）；④ A 机点过「重新扫描」了吗 |
| 配对成功但 B 机不注入 | B 机防火墙入站 UDP 33334 是否被放行（setup 已加规则，若手关过需重开）；B 机前台是否是已配置的 Agent（WorkBuddy/Trae） |
| 触发通了但 B 机听不到声音 | A 机 `voice-config.json` 的 `vban.target` 是否填了 B 机 IP；B 机是否装了 VB-CABLE 且设为默认录制设备 |
| 想换 B 机 | A 机托盘「重新扫描」→ 点新的配对项即可（自动覆盖旧 target） |

---

## 6. 给想自己编译的开发者

- B 机助手冻结：`windows/build/AI-Passport-btrigger-v0.4.0.spec`（纯标准库零依赖，`console=False` 后台运行）。
- 发布包打包：`tools/make_release_zip.py`（自动把 `btrigger/` 子目录、setup 脚本、`_internal` 运行时一并打进 zip）。
- beacon 协议：类型字节 `0x22` + JSON `{"role":"btrigger","host","ip","port"}`，广播到 `255.255.255.255:33335`。
- 自动发现监听端：`hotkey_forwarder._start_beacon_listener`（A 机侧，`--b-discover-port`，默认 33335，0=关闭）。
