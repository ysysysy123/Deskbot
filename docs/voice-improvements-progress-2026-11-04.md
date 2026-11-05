# Voice 改进实施记录

开始日期：2026-11-04。对应 [下一阶段计划](voice-next-improvements-plan-2026-11-02.md)。每项完成本地验证后单独提交和推送，远程 CI 结果另行核对。

## A. Voice CI 与说明同步

- `Repository checks` 新增 Voice 测试任务，使用 Python 3.11、Node.js 22、libopus、FFmpeg 和现有离线依赖，在 PR／main push 中运行 Python 与浏览器逻辑测试。
- Agent 接口说明同步现有工具后端、长期事实、合并系统消息、闲时整理、分段合成与流式转码；README 补齐测试环境和 CI 入口。
- 本地验证：全新临时 Python 虚拟环境仅安装 `requirements-test.txt`，375 项 Python 测试通过；Node.js 22 浏览器逻辑测试 16 项通过；workflow YAML 解析与差异检查通过。推送后继续核对远程 CI。

## 后续顺序

B. 通用工具结果播报 → C. TTS／首文字长尾 → D. 打断控制 → E／F. 实机闭环与设备计时。硬件验收以实测记录为准。
