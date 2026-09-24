# 与本地小智源码的语音功能对比

初次核对日期：2026-09-22，本地实现说明更新至 2026-09-24。参考目录为 `/home/yyy/desktop/ai/xiaozhi-esp32-server`，本实现为 `Deskbot/server/voice`。以下比较基于当前本地源文件，不涉及管理后台、机器人动作或其他项目模块。

文中的 `上游语音/` 指 `main/xiaozhi-server/`，`上游测试/` 指 `main/digital-human/`；本地源码简称相对 `server/voice/src/voice_server/`，启动脚本相对 `server/voice/`。

## 功能差别

| 能力 | 上游源码 | 本地实现与剩余差别 |
| --- | --- | --- |
| 设备语音与文本输入 | `上游语音/core/handle/textHandler/listenMessageHandler.py` 接收 `start/stop/detect` | 已支持 Opus WebSocket v1、手动停止、VAD 截止、`detect.text`；`stop/detect` 可省略 `mode`。见 `protocol/messages.py`、`session.py`。 |
| 打断 | `上游语音/core/handle/receiveAudioHandle.py` 可结合 `client_aec`、VAD 在回复期间触发打断 | 浏览器持续模式在生成及播放回复时继续 Silero VAD，连续约 180 ms 语音自动取消旧轮、清空旧播放并录入新一句；采集请求浏览器回声消除。原生 ESP32 连接未改：仍支持 `abort`、新文本或新 `auto/realtime start` 取消旧轮，尚无回复期间 VAD 打断。 |
| ASR | `上游语音/core/providers/asr/` 同时有本地、云端与流式实现，例如 `fun_local.py`、`doubao_stream.py` | 当前只有本地 SenseVoice；`ASRProvider.transcribe` 接收完整一轮 PCM，说完后才识别。流式识别需要扩展 Provider 契约，不能仅切换模型名。 |
| LLM 与 Agent | `上游语音/core/providers/llm/`、`intent/`、`tools/` 支持多后端、工具执行及多种 MCP 接入 | 当前为 OpenAI 兼容流式 LLM，可连接 Ollama。已预留 `DialogueBackend.stream(TurnRequest)`；`CameraMcpDialogueBackend` 已把本地测试页的摄像头 MCP 工具接入对话，拍照可交给独立的 `glm-4v-flash` 分析。通用 Agent 工具编排仍需扩展后端适配器。 |
| TTS 与响应延迟 | `上游语音/core/providers/tts/` 有 Edge、流式云端和本地服务适配器 | 当前只有 Edge TTS；LLM 接收与逐句合成通过有界队列并行，但每句音频收齐后才转码。已有 `TTSProvider` 输出 PCM 接口，可替换本地引擎；尚无真正流式音频合成。上游 Edge 实现也会收集音频，流式能力来自其他 Provider。 |
| 记忆 | `上游语音/core/providers/memory/` 有本地短记忆、Mem0、PowerMem 等选项 | 已有按设备隔离的 SQLite 消息、摘要、近期上下文和本机管理 API；`relevant_memories` 仍为空，尚无检索记忆。Agent 自管工具历史时可在后端适配器中实现。 |
| 唤醒词 | `上游测试/wakeword_runtime/core/detector.py` 使用 Sherpa ONNX；上游服务还区分唤醒问候与普通文本 | 本地接受设备传来的 `detect.text`，按普通对话处理；没有浏览器本地关键词检测或独立问候策略。设备端唤醒是否可用取决于固件。 |
| 声纹与说话人 | `上游语音/core/providers/asr/base.py` 可并行识别声纹，把说话人信息交给对话 | 尚无声纹识别；`Device-Id` 只表示设备，不表示该设备前不同的人。 |
| 音频格式 | 上游 Provider、握手及浏览器播放器有自己的采样率处理 | 本地设备输入固定 16 kHz、输出固定 24 kHz，均为单声道、60 ms 原始 Opus；不是 Ogg 文件。新测试页按本地参数适配。 |
| 配置 | `上游语音/config/config_loader.py` 合并 `config.yaml` 与 `data/.config.yaml`，也可从管理 API 读取 | 本地已支持所选 YAML 同目录 `.env`，优先级为系统环境变量 > `.env` > YAML > 默认值。全部配置可用 `VOICE_<SECTION>_<FIELD>`，无需引入上游管理 API。 |

本地实现的对应源码位于 [`src/voice_server/`](../src/voice_server/)，Agent 契约与取消行为见 [语音与 Agent 接口](voice-agent-integration.md)。

## 本地测试的复刻范围

上游当前的浏览器测试入口是 `main/digital-human/start.py`，不是旧版测试页面路径。其 `index.html` 和 `js/core/audio/`、`js/core/network/` 包含连接、文字、录音、播放与日志；同时包含数字人、摄像头、本地唤醒及 MCP 模拟。模型测速则是独立的 `main/xiaozhi-server/performance_tester.py`。

本次在语音目录增加独立的 `local_test.py` 与浏览器页面，复刻语音联调操作；页面通过 `src/voice_server/local_testing.py` 连接真实语音服务。浏览器发送 16 kHz PCM，桥接服务使用本机 Opus 编码；服务回复的 24 kHz Opus 解码后在浏览器播放。这样不需要浏览器 WASM 编解码器或外部资源。

已实现的测试操作：

- 连接与断开、设备 ID、可选设备 Token、文字对话、手动麦克风录音；单次最长 30 秒，服务配置更短时使用其上限。
- WAV 上传：浏览器解码、混为单声道、重采样到 16 kHz，再按 60 ms 实时发送。
- 24 kHz 顺序播放与主动打断；显示识别结果、回复、首个 STT 与首包音频耗时、收到音频总时长。
- 手动检查语音、OTA、LLM 连接与本机依赖；保存最近 400 条事件供文本导出，Token 不进入日志。

不能直接把上游页面地址指向本地设备服务，原因有三项源码差异：

1. 上游 `js/core/network/ota-connector.js` 把 `device-id/client-id/authorization` 放在 URL 查询参数中；本地 `websocket_server.py` 使用请求头并匹配固定 `/xiaozhi/v1/` 路径。
2. 上游 `js/core/network/websocket.js` 的浏览器 `hello` 不携带完整 v1 音频字段；本地 `protocol/messages.py` 要求声明格式、采样率、通道数与帧长。
3. 上游 `js/core/audio/player.js` 默认播放采样率为 16 kHz；本地回复是 24 kHz，必须按实际音频参数播放。

测试桥在服务端适配这些差异，设备原有协议保持不变。普通回复结束应让已排队音频播放完毕，主动打断才清空播放队列。

本地测试已增加摄像头预览、拍照与保存快照，以及麦克风长期保持开启的持续对话模式。持续对话按 Silero VAD 分轮，回复期间连续约 180 ms 语音可自动打断；浏览器请求回声消除，实际效果依赖设备与浏览器，可用耳机测试。

摄像头通过本机 `/mcp` 注册 `self_camera_start`、`self_camera_take_photo`、`self_camera_stop` 和 `self_camera_status`，工具绑定当前浏览器的语音会话。`CameraMcpDialogueBackend` 对明确的开关、拍照和观察指令直接调用 MCP；“打开摄像头看看看到了什么”等观察请求会自动开启摄像头、等待画面并拍照。独立视觉模型 `glm-4v-flash` 分析 JPEG，明确观察请求的视觉描述直接交给 TTS；其他对话仍可通过模型工具循环操作摄像头，并将视觉结果交给 `glm-4-flash` 组织回复。`AppConfig` 默认关闭 MCP 与视觉能力，本机通过 `.env` 启用，完整字段见 [摄像头配置](local-testing.md#摄像头)。数字人、本地关键词唤醒和通用 Agent 工具编排仍未实现。上游测速还包含流式 ASR、流式 TTS 与视觉模型；本地目前仅有基础 ASR/LLM/TTS 测速。

本地 [`performance_tester.py`](../performance_tester.py) 已提供 `asr/llm/tts/all` 四个子命令。例如：

```bash
.venv/bin/python performance_tester.py llm --repeat 3 --text '用一句话介绍你自己' --json data/performance.json
.venv/bin/python performance_tester.py all --audio sample.wav --repeat 3
```

测速读取同一份配置及 `.env`，报告 Provider 加载、准备、首响应与总耗时；ASR/TTS 另含音频时长与实时率，LLM 包含文本字符数及分块数。字符数不等于 token 数。当前 TTS 的首响应指整句下载和转码完成后产生的首段 PCM，不能据此宣称逐块流式合成。尚未复刻多供应商排行榜、流式 ASR/TTS 或视觉模型测速。

## 当前机器与真实运行前提

| 用途 | 当前地址或状态 |
| --- | --- |
| 本机浏览器测试 | `http://127.0.0.1:8006/`；本机打开，便于浏览器授予麦克风权限 |
| ESP32 语音 WebSocket | `ws://192.168.10.111:8000/xiaozhi/v1/` |
| ESP32 OTA 配置地址 | `http://192.168.10.111:8003/xiaozhi/ota/` |
| 本机记忆管理 API | `http://127.0.0.1:8004/` |
| 当前 LLM | 按用户指定使用智谱 `glm-4-flash`，地址及密钥放在本机 `.env` |
| 可选本机 Ollama | `http://127.0.0.1:11434/v1`；检查时模型列表为空，不影响使用智谱 |
| 网络接口 | LAN 为 `enp59s0` 的 `192.168.10.111/24`，网关 `192.168.10.1`；不要使用代理 TUN 的 `198.18.0.1` 作为设备地址 |

源码齐全不代表模型及运行环境自动齐全；当前本机已补齐并验证上游 Silero ONNX、SenseVoice `model.pt`、FunASR、Torch、ONNX Runtime、Edge TTS 与 FFmpeg。LLM 已按用户要求改用智谱，密钥仅保存在本机 `.env`，不放入文档或示例。

测试页面可以独立启动并检查依赖、连接状态；当前已完成真实 ASR、Edge TTS 和一轮 WebSocket 文本对话验收。协议自动测试仍使用模拟 ASR/LLM/TTS 验证消息和音频传输，不代表识别准确率、外网 TTS 或模型延迟已经全面验收。

## 后续优先顺序

1. **真实链路已跑通**：使用上游 SenseVoice 权重、Conda Python 依赖与 FFmpeg 完成 ASR、TTS 和文本对话验证。
2. **再减少首包等待**：先测首段文本和首包音频耗时；优先替换可逐块输出 PCM 的 TTS，再按实测结果决定是否增加流式 ASR。
3. **接入 Agent**：实现 `DialogueBackend` 适配器，让工具与规划留在 Agent 内，仅向语音层输出可朗读文本；同时传递取消，避免打断后继续播报旧任务。
4. **按交互需求扩展**：把浏览器已有的 VAD 自动打断扩展到原生 ESP32 连接，并结合设备端回声处理验证；需要多人身份时再增加声纹。
