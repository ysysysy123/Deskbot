# Deskbot

Deskbot 是一个可自托管、可扩展的小型桌面陪伴机器人项目。目标是先完成一个能听、能说、能表达、能执行简单动作的 MVP，再逐步加入视觉理解、长期记忆和个性化角色能力。

> 当前仓库是协作骨架，不包含未经验证的硬件引脚、密钥或可直接量产的实现。

## MVP 范围

- ESP32 端：麦克风、扬声器、屏幕、灯光和电机的基础驱动与统一服务接口。
- 服务端：`ASR → LLM → TTS` 语音链路，以及独立的图像理解入口。
- 协议：ESP32 与服务端共享版本化消息格式；MVP 先走 WebSocket 控制通道。
- 机械与测试：完成桌面外壳、装配、电气安全检查和端到端验收。

暂不纳入 MVP：复杂角色编辑器、长期记忆、多机器人集群、移动端管理后台、云端多租户和自动 OTA。

## 仓库地图

```text
Deskbot/
├─ firmware/          # ESP32：底层、服务、调度
├─ server/
│  ├─ voice/          # ASR / LLM / TTS
│  └─ vision/         # 图像理解
├─ protocol/          # 双端共享协议与示例
├─ mechanical/        # 机械、装配和 BOM
├─ docs/              # 架构、分工、路线图和决策记录
└─ .github/           # Issue、PR 和自动检查
```

详细说明：

- [系统架构](docs/architecture.md)
- [团队分工与 GitHub 工作流](docs/team-workflow.md)
- [MVP 路线图](docs/roadmap.md)
- [协议约定](protocol/README.md)

## 协作方式

1. 从 GitHub Issue 领取一项任务，并写清验收标准。
2. 从 `main` 创建短分支：`feat/<scope>-<topic>`、`fix/<scope>-<topic>` 或 `docs/<topic>`。
3. 一次 PR 只解决一个 Issue；协议变更必须同时更新 schema、示例和文档。
4. 至少由受影响模块的一名成员审核后合并。

第一次参与请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 第一个集成目标

在 PC 本地运行最小服务端，ESP32 通过 WebSocket 建立连接；服务端发送 `device.command`，设备返回 `device.event`。先用串口日志或开发板 LED 模拟动作，不等待完整机械结构与云端语音服务。

## 参考项目

本仓库只借鉴架构思想，不复制参考项目代码：

- [Project AIRI](https://github.com/moeru-ai/airi)：自托管数字伙伴、角色呈现与模块化能力。
- [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)：ESP32 语音、显示、设备控制与通信链路。
- [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)：语音服务、管理模块与端到端数据流。

## 许可证

项目许可证尚未决定。在许可证确定前，请勿将仓库内容对外再发布。参考项目各自的许可证不自动适用于 Deskbot。
