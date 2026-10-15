# 来源和许可证

本仓库只覆盖随身语音应用。根 LICENSE 保留原有 MIT 声明，不表示提供整机其余代码或其授权。

## 本项目（folo-ai-passport-voice-agent）的血缘声明

本项目是 [manchunx7-bit/folo-ai-passport-voice](https://github.com/manchunx7-bit/folo-ai-passport-voice) v0.2.0 应用包的二次开发版本，感谢上游作者发布的完整 Windows 源码与教程。本项目在其基础上新增"通用 Agent 模式"（详见 README 与 CHANGELOG），全部改动同样以 MIT 协议回馈社区。

血缘链（均为 MIT）：
- 硬件基础：[FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)
- 基础语音实现：[zhaohuaxiaoy/folo-ai-passport-voice](https://github.com/zhaohuaxiaoy/folo-ai-passport-voice)
- 上游应用包（本项目的直接基础）：[manchunx7-bit/folo-ai-passport-voice](https://github.com/manchunx7-bit/folo-ai-passport-voice)
- 本项目：folo-ai-passport-voice-agent（通用 Agent 模式二创）

- 基础语音实现：[zhaohuaxiaoy/folo-ai-passport-voice](https://github.com/zhaohuaxiaoy/folo-ai-passport-voice)，MIT；硬件基础参考 [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)，MIT。
- 本地应用生命周期、UDP/按键处理、Windows 适配和文档按根 LICENSE 分发。
- Windows 运行时涉及 Python、sounddevice、CFFI、PortAudio、PyInstaller，随附原文在 `licenses/`；各自许可证仍适用。
- 设备端宿主使用 ESP-IDF、LVGL 等公开依赖时，应自行保留其许可证；本项目不打包这些库和维护者整机的共享实现。
- VB-CABLE 与微信输入法是各自厂商的软件，安装程序不在本包内，从官网获取。

项目为社区作品，不代表 FoloToy、腾讯或 VB-Audio 官方支持。个人配置、录音和账号资料不属于公开内容。
