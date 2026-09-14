# Agent Instructions

任何 ChatGPT、Codex 或其他 Agent 开始工作前必须先读 `GPT_HANDOFF.md`，再读与任务相关的架构、协议和模块文档。

## 工作边界

- 先检查 `git status` 和 diff，不覆盖用户已有改动。
- `Deskbot` 与 `Deskbot1` 是指向同一 GitHub 仓库的不同本地副本；使用不同 `ai/*` 分支，不在两个副本中同时改同一分支。
- 不提交 `.env`、凭据、设备密钥、令牌、个人数据、构建目录、日志或录音。
- 大文件放 Google Drive，并在 `ARTIFACTS.md` 登记链接、SHA-256、大小、用途和访问范围。
- 每轮交接前更新 `GPT_HANDOFF.md`，记录完成项、验证、阻塞和下一步。

## 发布

只能明确暂存需要共享的文件。可使用 `scripts/publish-handoff.ps1` 提交，并仅在用户要求时加 `-Push`。
