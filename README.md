# AI Passport 随身语音 · Agent 模式（folo-ai-passport-voice-agent）

## 这是一个什么项目

作者平时在电脑上用 **WorkBuddy / Trae** 这类 AI 工具做 vibe coding 和日常知识工作，跟 AI 对话打字是最高频的动作。手里有枚 FoloToy AI Passport（巴掌大的语音小钥匙），自然就想到：**按住它说话，话直接变成聊天框里的文字**——不用打字，也不用凑在电脑麦克风前。

上游项目 [manchunx7-bit/folo-ai-passport-voice](https://github.com/manchunx7-bit/folo-ai-passport-voice)（v0.2.0，MIT）已经把 AI Passport 接入了微信输入法。本仓库在其基础上二次开发：**把 AI Passport 接入 WorkBuddy 和 Trae**——按住设备上键说话，松开自动停止，文字进入 AI 应用的聊天输入框，短按下键回车发送。

> **本仓库是 [manchunx7-bit/folo-ai-passport-voice](https://github.com/manchunx7-bit/folo-ai-passport-voice)（v0.2.0，MIT）的二创版本，衷心感谢上游作者与 FoloToy 社区。** 完整血缘声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
>
> **本仓库只覆盖随身语音功能，不含整机源码或可刷写固件。** 设备固件请从 [FoloToy 口袋百宝箱玩法页面](https://ai-passport.folotoy.cn/plays/438/) 获取，不要把本仓库 Source code ZIP 当固件。

## 面向谁

- 和作者一样，用 **WorkBuddy 或 Trae（CN）** 做 AI 编码 / 对话工作，想给 AI 应用加一枚无线语音键的人；
- 前置条件与上游一致：已拥有 FoloToy AI Passport（ESP32-C3 / 8MB / ES8311），并且已在设备上烧好[口袋百宝箱玩法页](https://ai-passport.folotoy.cn/plays/438/)的「随身语音」固件——这一步是使用本项目的前提，先做它，再回来配电脑端。

## 两种工作模式

| 模式 | 适用对象 | 原理 |
| --- | --- | --- |
| **输入法模式**（上游原版） | 微信输入法等系统输入法 | 注入输入法的语音快捷键（如右 Shift），由输入法录音转写 |
| **Agent 模式**（本项目新增 ⭐） | WorkBuddy、Trae（CN）等任何"有语音快捷键的 AI 应用" | 按前台/最近使用的应用自动注入它自己的语音快捷键（WorkBuddy=`Ctrl+D`、Trae=`Alt+V`），由应用自带的语音能力录音转写 |

Agent 模式的核心能力（这也是本项目的架构卖点）：

- **任何场景按键即录音**：内置焦点观察器持续记住"你最近在用的 AI 应用"。哪怕焦点被你正在开发的程序、终端、浏览器抢走，按设备键也会确定性切回目标应用并恢复输入框焦点——不要求你"干净地"使用电脑。
- **不依赖特殊启动方式**：CDP 直连（Trae 调试端口）只是可选加速通道；没有它，键盘注入路径同样工作。
- **配置驱动接入新应用**：改一份 `windows/voice-config.json` 就能让新的 AI 应用获得语音键支持（见下文"接入新应用"）。

## 环境要求

- Windows 10/11 x64；Python 3.10+（推荐 3.11+，含 Python launcher `py`）
- FoloToy AI Passport（ESP32-C3 / 8MB / ES8311），已刷入官网玩法页的"随身语音"固件
- [VB-CABLE 虚拟声卡](https://vb-audio.com/Cable/)（设备声音 → 电脑的桥梁）
- 电脑与设备在同一 2.4 GHz 局域网（USB 线只用于充电/刷机，不传语音）

## 快速开始

```text
1. 安装 VB-CABLE（官网下载，装完重启一次电脑）
2. 双击 windows/install.cmd        ← 自动建 Python 虚拟环境并装依赖
3. 双击 windows/diagnose.cmd       ← 自检：VB-CABLE 两端 / UDP 33333 / 快捷键
4. 双击 windows/start-agent.cmd    ← 启动转发器（窗口可最小化，日志在 forwarder.log）
5. 设备上打开「AI语音」应用 → 点进 WorkBuddy/Trae 聊天框 → 按住上键说话 → 松开出字 → 下键发送
```

首次验收建议先在记事本或 WorkBuddy 里试，不要在重要聊天窗口直接测试。

## 让本地 AI 帮你部署

和上游的"让本地 Codex 帮忙配置"一样，本仓库支持把配置工作交给 AI：仓库根目录的 [AGENTS.md](AGENTS.md) 是写给 AI 的部署手册，主流 AI 编码工具会自动读取并按步骤执行。克隆本仓库后，在仓库目录里对 AI 说——

> 先阅读 README.md 和 AGENTS.md。只帮我配置随身语音电脑端，不要重建或公开整机源码。依次执行：① 运行 windows\install.cmd 装依赖并核对 VB-CABLE 两端、UDP 33333 和快捷键；② 复制 voice-config.example.json 为 voice-config.json，按我要接入的 AI 应用改 apps 列表；③ 跑 diagnose.cmd 自检；④ 最后让我在记事本亲自说话，确认松手结束、文字出现、下键换行。未实际验证的环节明确说明，不要自动把环境声音发到云端。

### 适用范围（如实说明）

- 本项目为作者的真实场景而做：**面向并实际验证过的应用是 WorkBuddy 与 Trae（CN）**；
- 其他 AI 应用（Cursor 等）可通过 `apps` 配置接入（机制上支持），但作者未实测，不承诺兼容；
- 本项目**不提供**语音识别账号或 ASR 服务——转写完全依赖目标 AI 应用自带的语音输入能力，因此也不承诺其所有版本兼容；
- Windows 程序要求 VB-CABLE 虚拟声卡；设备端为功能模块源码，不是单独可刷写的 ESP-IDF 工程（见适配说明）；
- 个人配置、录音与输入法账号不要提交到 GitHub。

## 配置：windows/voice-config.json

首次使用复制 `voice-config.example.json` 为 `voice-config.json` 再改。默认已内置 WorkBuddy 与 Trae CN 的 Agent 配置：

```jsonc
{
  "udp_port": 33333,                      // 设备 UDP 端口，与固件一致
  "output_substr": "CABLE Input",         // 设备音频写入的回放设备
  "trigger_mode": "mic",                  // mic=Agent/输入法快捷键模式
  "apps": [                               // Agent 模式的应用清单，按需增删
    {"title_contains": "WorkBuddy", "exe_contains": "WorkBuddy",
     "voice_key": "ctrl+d", "stop_on_release": true},
    {"title_contains": "Trae", "exe_contains": "Trae CN",
     "voice_key": "alt+v", "stop_on_release": true, "cdp_port": 9222}
  ]
}
```

### 接入新的 AI 应用（比如 Cursor）

1. 在目标应用里找到它"开始/停止语音输入"的快捷键
2. 在 `apps` 里加一条：`{"title_contains": "应用窗口标题关键词", "exe_contains": "进程名关键词", "voice_key": "它的快捷键"}`
3. 重启转发器即可。应用识别优先匹配前台窗口标题或进程名（不区分大小写）。

## 按键说明（设备端）

- **按住上键**：开始语音（应用开始录音）
- **松开上键**：结束录音（`stop_on_release` 应用自动停录转写）
- **短按下键**：发送 Enter（对聊天应用=发送消息）
- **长按确定**：退出设备「AI语音」应用

## 与上游版本的差异

详见 [CHANGELOG.md](CHANGELOG.md)。要点：

- 新增通用 Agent 模式（`apps` 配置、多应用自动切换、焦点观察器、确定性窗口找回、UIA 输入框焦点恢复）
- 新增 Trae CDP 直连通道（可选，严格页面校验防止误连其他 Chromium 程序）
- 修复键盘回退路径在"窗口切回但输入框失焦"场景下快捷键被忽略的问题
- 补齐 Python 依赖清单（pywinauto / websockets）

## 排错

先跑 `windows/diagnose.cmd`。常见问题（按住上键没反应、识别不到应用、快捷键被抢焦点等）见 [docs/TROUBLESHOOTING.zh-CN.md](docs/TROUBLESHOOTING.zh-CN.md)。

## 文档

| 文档 | 讲什么 |
| --- | --- |
| [详细使用教程](docs/QUICKSTART.zh-CN.md) | **输入法模式**（上游原版流程：微信输入法 + EXE 应用包），含固件烧录、设备配网、VB-CABLE 详解——只想用输入法模式的看它 |
| [常见故障排查](docs/TROUBLESHOOTING.zh-CN.md) | 快捷键 / 端口 / 设备发现 / 网络问题，两种模式通用 |
| [开发与源码运行](docs/DEVELOPMENT.zh-CN.md) | 不用 EXE、直接跑 Python 源码 |
| [固件附录](docs/FIRMWARE.zh-CN.md) | 本地烧录、备份与恢复 |
| [适配说明](docs/PORTING.zh-CN.md) | 设备端功能模块与宿主接口适配 |
| [验证记录及限制](docs/VALIDATION.md) | 上游已验证环境与未覆盖范围 |
| [第三方来源](THIRD_PARTY_NOTICES.md) | 完整血缘与版权声明 |

## 参与贡献

欢迎 Issue / PR。接入新应用的配置样本、真机验证结果都特别欢迎（请标注你的验证环境）。提交前跑 `windows/tests/` 下的测试，并遵守 MIT 协议。

## 许可证

MIT。本项目为社区作品，不代表 FoloToy、腾讯或 VB-Audio 官方。个人配置、录音与账号资料不要提交到 GitHub。
