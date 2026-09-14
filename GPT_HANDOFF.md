# GPT / Codex Handoff

本文件是网页版 ChatGPT 与本地 Codex 的共享入口。接手任务时先读 `AGENTS.md`、本文件和相关模块文档。

## 仓库身份

- 项目：Deskbot
- GitHub：`https://github.com/ysysysy123/Deskbot`
- 本地工作副本：`Deskbot`
- 建议交接分支：`ai/deskbot-handoff`
- 注意：另一个本地副本 `Deskbot1` 指向同一远端；两边不得共用交接分支或互相覆盖未提交改动。

## 当前状态

- 主线目标：桌面陪伴机器人，包含 ESP32 固件、语音/视觉服务、协议与机械结构。
- 本副本存在进行中的固件、音频服务和本地音乐网关改动；接手前必须先看 `git status` 和实际 diff。
- 本文件不宣称这些进行中改动已完成或通过测试。

## 本次交接

- 目标：待每次任务结束时更新。
- 已完成：待更新。
- 验证结果：待更新。
- 未解决问题：待更新。
- 需要网页版决策：无；有问题时写成可直接回答的条目。
- Codex 下一步：先读取 diff，再根据网页版结论实施和测试。

## 交接规则

1. Codex 完成一轮实现后，更新本节，只写当前事实，不粘贴大段源码或聊天记录。
2. 代码、配置模板和 Markdown 进入 GitHub；Secret、令牌、本地配置和生成物不得提交。
3. 大型二进制放入本地 `artifacts/`，上传 Google Drive 后把链接、SHA-256 和大小登记到 `ARTIFACTS.md`。
4. 使用独立 `ai/*` 分支推送；网页版基于该分支或 PR 做分析与决策。
5. 网页版结论应落回本文件的“需要网页版决策”或 GitHub Issue/PR，Codex 再执行。

## 安全发布

只提交明确列出的路径：

```powershell
.\scripts\publish-handoff.ps1 -Message "docs: update AI handoff" -Paths GPT_HANDOFF.md,ARTIFACTS.md -Push
```

脚本拒绝已有暂存内容、绝对路径和常见凭据文件；它不会自动把整个工作区加入提交。
