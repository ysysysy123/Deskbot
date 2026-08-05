# 团队分工与 GitHub 工作流

下表对应当前 6 人协作框架。GitHub 用户名确定后，在 Issue 中指派负责人；暂不写死 CODEOWNERS，避免错误授权。

| 角色代号 | 建议人数 | 主责目录 | 第一阶段交付 |
| --- | ---: | --- | --- |
| `PM-MECH-QA` | 1 | `mechanical/`, `docs/roadmap.md` | 外壳约束、BOM、周计划、回归清单 |
| `FW-HAL` | 1 | `firmware/hal/` | 麦克风、扬声器、屏幕、灯光、电机的稳定接口 |
| `FW-SERVICE` | 1 | `firmware/services/` | 表情、灯效、动作和播放服务 |
| `FW-ORCH` | 1 | `firmware/orchestrator/` | 状态机、通信接入、在线/离线调度 |
| `BE-VOICE` | 1 | `server/voice/` | PC 本地 ASR → LLM → TTS 闭环 |
| `BE-VISION` | 1 | `server/vision/` | 图像输入、理解结果和协议接入 |

`FW-ORCH` 与一名服务端集成负责人共同维护 `protocol/`。服务层和调度层虽然目录独立，但两名固件成员需要共同评审跨层接口。

## Issue 规则

每个 Issue 必须包含：

- 背景和明确边界；
- 可验证的验收标准；
- 依赖项和硬件要求；
- 唯一主负责人；
- 所属里程碑。

任务控制在 0.5～3 个工作日。更大的工作先拆分，不用一个长期分支承载整个模块。

## 分支与审核

```text
main
 ├─ feat/firmware-hal-audio
 ├─ feat/server-voice-loop
 ├─ feat/protocol-device-command
 └─ docs/mechanical-bom
```

- `main` 始终保持可检查、可集成。
- 普通模块 PR：至少 1 名相关模块成员审核。
- `protocol/` PR：至少固件端和服务端各 1 名成员审核。
- 机械改动若影响引脚、功耗、散热或尺寸，必须通知 `FW-HAL`。
- 每周用一个 GitHub Milestone 汇总目标，不额外引入项目管理工具。

## 推荐标签

| 标签 | 用途 |
| --- | --- |
| `scope:firmware` | ESP32 固件 |
| `scope:server` | 语音或视觉服务 |
| `scope:protocol` | 跨端接口 |
| `scope:mechanical` | 结构、BOM、装配 |
| `type:task` | 功能任务 |
| `type:bug` | 缺陷 |
| `blocked` | 有明确外部依赖 |
| `needs-hardware` | 需要实机验证 |

## 每周节奏

1. 周初：从路线图选择本周最小可演示目标并拆 Issue。
2. 开发中：尽早提交 Draft PR，接口争议记录到 `docs/decisions/`。
3. 周末：在同一硬件版本上做端到端演示和回归，关闭已验收 Issue。
