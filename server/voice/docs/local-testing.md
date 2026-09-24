# 本地语音测试

本目录复刻上游语音测试所需的浏览器交互与模型测速，具体差别见 [上游功能对比](upstream-voice-comparison.md)。测试页面独立运行，不要求先加载语音模型；对话时连接真实 `run.py` 服务。

## 本机配置

已生成本机 `config.yaml` 与 `.env`，两者均被 Git 忽略。后续把模型、密钥、地址和程序路径写进 `.env` 即可，系统环境变量优先；修改后重启对应进程。

| 用途 | 地址 |
| --- | --- |
| 电脑浏览器测试 | `http://127.0.0.1:8006/` |
| ESP32 OTA | `http://192.168.10.111:8003/xiaozhi/ota/` |
| ESP32 WebSocket | `ws://192.168.10.111:8000/xiaozhi/v1/` |
| 记忆管理 | `http://127.0.0.1:8004/` |
| 摄像头 MCP | `http://127.0.0.1:8006/mcp` |
| LLM | 智谱 `glm-4-flash`，`https://open.bigmodel.cn/api/paas/v4` |
| 摄像头视觉模型 | 智谱 `glm-4v-flash`；与文字对话模型分别配置 |

局域网地址取自 `enp59s0`；IP 变化后更新 `VOICE_SERVER_PUBLIC_WEBSOCKET_URL`。测试桥默认在本机回环地址连接语音服务，不经过公网。当前本地 VAD、ASR 路径指向上游模型目录，因此不要移动该目录；迁移时修改 `VOICE_VAD_MODEL_PATH`、`VOICE_ASR_MODEL_PATH`。

## 启动

在 `server/voice` 打开两个终端：

```bash
# 终端一：真实语音服务
conda activate xiaozhi
python run.py --config config.yaml
```

```bash
# 终端二：独立测试页面
conda activate xiaozhi
python local_test.py
```

两个终端使用同一个 `xiaozhi` 环境，确保测试页的依赖诊断与真实服务一致。可以用 `--config /path/config.yaml` 选择配置，加载该 YAML 同目录的 `.env`。

浏览器在本机打开 `http://127.0.0.1:8006/`：

1. 点击检查服务，查看依赖及服务连通性。端口通不等于模型推理已通过；部分兼容服务不提供模型列表，应以实际 LLM 测速为准。
2. 填设备 ID 后连接。启用设备 Bearer 认证时可填 Token，留空则测试桥读取 `.env` 中配置的设备 Token；页面不持久保存 Token。
3. 发送文字可跳过 ASR，验证 LLM、记忆与 TTS；麦克风录音结束或上传 WAV 后验证完整语音链路。
4. 回复中点击打断，检查旧播放停止后可以发起新一轮。普通回复结束会播放完已接收音频。
5. 观察转写、回复、耗时及日志，需要时导出日志。

## 持续麦克风对话

连接后开启持续对话并允许使用麦克风。麦克风会保持开启，说完一句停顿后自动提交，时长由 `VOICE_VAD_MIN_SILENCE_DURATION_MS` 控制（当前 600 ms）；回复播放完后自动等待下一句，也可以手动提交当前一句。长时间不说话不会累积成识别请求；ASR 只返回标点时跳过模型回复，继续监听。

持续模式没有总时长限制，每一轮仍遵守最多 30 秒（或服务配置的更短上限），到达上限自动提交。模型生成和扬声器播放期间仍持续检测语音，Silero VAD 判定连续约 180 ms 语音后自动打断旧回复，清空旧播放并开始接收新一句；无需先点击打断按钮。仍可手动打断。停止持续模式会释放麦克风，断开连接或关闭页面也会停止采集。

测试桥使用本地 Silero 模型与静音时长划分轮次；页面同时请求浏览器回声消除和降噪。VAD 判断是否有人说话，回声消除负责减少扬声器声音回录，两者作用不同；实际效果依赖浏览器、麦克风和扬声器，可先戴耳机测试自动打断。此改动仅作用于浏览器测试桥，原生 ESP32 连接尚未增加回复期间的 VAD 打断。电脑休眠、网络中断或浏览器冻结后台页面仍会影响会话。

## 摄像头

摄像头可以独立于语音连接开启，支持实时预览、拍照和保存快照；关闭摄像头或页面后释放摄像头。手动预览与快照在当前浏览器处理。对话调用拍照工具时，单张 JPEG 经本机 MCP 传给对话后端；启用视觉模型后，该图像还会发往配置的视觉服务，不持续上传视频。

`CameraMcpDialogueBackend` 已把摄像头工具接入实际对话。保持测试页打开并连接语音服务，可说或输入“打开摄像头”“看看我手里是什么”“关闭摄像头”。首次使用需允许浏览器摄像头权限。工具绑定当前语音会话，多标签页时不会任意选择其他页面的摄像头。

明确的开关、拍照和观察指令由后端直接调用 MCP，根据实际执行结果回答。“打开摄像头看看看到了什么”“你看到了什么？”等观察请求会直接拍照；摄像头关闭时，拍照工具先自动开启摄像头，并等待首帧画面就绪。照片交给视觉模型分析，实际描述直接交给 TTS，不再让文字模型决定是否执行或重写视觉结果。其他对话仍可通过模型工具调用处理。

文字对话继续使用 `glm-4-flash`，图像由独立的 `glm-4v-flash` 处理。明确观察请求直接播报视觉描述，普通模型工具循环则将视觉结果交回文字模型。`AppConfig` 默认关闭 MCP 和视觉能力；本机测试在 `.env` 中启用，迁移时可使用以下配置：

```dotenv
VOICE_MCP_ENABLED=true
VOICE_MCP_URL=http://127.0.0.1:8006/mcp
VOICE_MCP_TIMEOUT_SECONDS=20
VOICE_MCP_MAX_ROUNDS=3
VOICE_VISION_ENABLED=true
VOICE_VISION_MODEL=glm-4v-flash
VOICE_VISION_BASE_URL=
VOICE_VISION_API_KEY=
VOICE_VISION_TIMEOUT_SECONDS=60
```

`VOICE_VISION_BASE_URL` 和 `VOICE_VISION_API_KEY` 留空时分别继承 `VOICE_LLM_BASE_URL` 和 `VOICE_LLM_API_KEY`，无需重复填写智谱密钥。更换视觉服务时可独立设置地址、密钥和模型。`VOICE_MCP_MAX_ROUNDS` 限制单轮对话的工具调用轮数；超时单位均为秒。修改后重启语音服务与测试页服务。只启用 MCP 而未启用视觉时，仍可控制摄像头、拍照，但不能解读图像。

MCP 服务地址为 `http://127.0.0.1:8006/mcp`，提供 `self_camera_start`、`self_camera_take_photo`、上游兼容别名 `self.camera.take_photo`、`self_camera_stop` 和 `self_camera_status`；拍照结果以 MCP image content 返回，图片不经过语音 WebSocket。外部 Agent 可使用 JSON-RPC `initialize`、`tools/list`、`tools/call` 访问它；多个测试页同时打开时，通过参数 `_meta` 中的 `deskbot/session_id` 指定语音会话。

麦克风和摄像头必须使用本机 `localhost/127.0.0.1` 或 HTTPS 安全上下文；直接访问局域网 HTTP 地址可能无法取得采集权限。单轮录音最长跟随 `VOICE_SERVER_MAX_RECORDING_SECONDS`，默认 30 秒，持续对话没有总时长限制。

页面发送 16 kHz 单声道 PCM，测试桥编码为设备协议的 60 ms 原始 Opus；回复按 24 kHz 解码播放。此方式验证真实协议和语音服务，但不会测试 ESP32 的拾音、回声、喇叭或 Wi-Fi 稳定性。

## 模型测速

```bash
conda activate xiaozhi
python performance_tester.py llm --repeat 3 --json data/llm-benchmark.json
python performance_tester.py asr --audio samples/speech.wav --repeat 3
python performance_tester.py tts --text '你好，这是语音合成测试。' --repeat 3
python performance_tester.py all --audio samples/speech.wav --json data/performance.json
```

`--json -` 输出纯 JSON。测速使用真实 Provider，失败返回非零退出码；不下载模型，不用模拟回复代替。结果分别记录初始化、首响应和总耗时；ASR/TTS 还有音频时长与实时率。当前 ASR 是整段识别，Edge TTS 每句音频收齐后转码；首响应指标按这一实际边界计算。

## 当前依赖状态

`xiaozhi` Conda 环境已安装 Python 3.11、CPU Torch 2.2.2/torchaudio 2.2.2、FunASR 1.2.7、ONNX Runtime 1.30.0、Edge TTS 7.2.6、FFmpeg 8 与 libopus。Silero ONNX 和 SenseVoiceSmall（含 `model.pt`）均复用上游模型目录。2026-09-23 已用中文示例音频完成真实 ASR，并通过 8003 health/OTA 检查；Edge TTS 已生成 `data/check-tts-conda.wav`。

2026-09-22 已使用本机 `.env` 对智谱 `glm-4-flash` 发起一次真实流式调用：首段文本约 847 ms，总耗时约 1173 ms；结果在本机 `data/glm-benchmark.json`。这是单次观测，不是稳定延迟承诺。智谱 `/models` 列表未包含该旧模型，但实际调用成功，因此测试页只把列表作为辅助诊断。

独立页面会显示当前依赖状态。离线自动测试使用真实 WebSocket、Opus 和模拟模型，覆盖文字、录音分帧、尾帧补齐、回复与打断；它不代表真实识别或外网合成已通过。

2026-09-23 已通过连续两轮真实 ASR → 智谱 GLM → Edge TTS 测试，结果在本机 `data/continuous-acceptance.json`；浏览器实际麦克风持续运行超过 100 秒，完成多轮回复后自动恢复监听，并成功开启摄像头预览。此测试不等于数小时稳定性验收。

2026-09-24 使用中文录音以实时 PCM 帧进入测试桥，验证真实 Silero → ASR → GLM → TTS 链路：首轮 TTS 未结束时，第二段话音起始约 362 ms 后触发打断，随后新一轮识别和回复正常；静音未触发打断，旧音频未混入新轮。结果在本机 `data/vad-barge-in-acceptance.json`。这是录音输入验收，尚未验证当前扬声器与麦克风组合的回声效果。

同日通过真实浏览器验证对话打开摄像头、拍照后视觉回答及关闭释放设备；两页同时连接时工具只发送给绑定会话。结果摘要在 `data/camera-dialogue-acceptance.json`，不含照片。

随后针对组合指令补充验收：摄像头关闭时上传“打开摄像头看看看到了什么”的语音样本，实际 ASR 正确转写、直接拍照工具自动开启摄像头并返回画面描述，首段回复约 4.49 秒；持续麦克风模式也实际转写了“你打开摄像头看看有什么。”并完成同样流程。结果记录于 `data/camera-combined-acceptance.json`，不含图像。当前离线测试为 305 项 Python 与 10 项浏览器逻辑测试通过。

```bash
.venv/bin/python -m pytest -q
node --test tests/browser/local_test.test.cjs
```

持续模式测试覆盖静音等待、自动分轮、播放结束后恢复、空识别和合成失败后的恢复、连接保活；浏览器逻辑测试还覆盖长时间采集、手动提交、打断保留麦克风、摄像头快照和关闭后释放设备。自动测试使用模拟媒体设备，实际硬件仍需在本机允许权限后验证。
