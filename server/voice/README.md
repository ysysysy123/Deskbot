# 小智 ESP32 语音 Server

这是一个从零实现的单进程 Python Server，兼容小智 WebSocket 二进制协议 v1：ESP32 上传无容器 Opus，服务端按 `VAD → SenseVoice ASR → SQLite 记忆 → OpenAI 兼容 LLM → Edge TTS → Opus` 处理。默认部署目标是可信局域网，设备以 `Device-Id` 隔离记忆。

语音服务的后续演进参考 [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 的消息处理、Provider 分层和 TTS 队列设计。当前默认通过 LLM 对话，也可注入 `DialogueBackend` 接入 Agent。实现边界、配置和接入示例见 [语音架构与 Agent 接口](docs/voice-agent-integration.md)。

当前本机测试入口、网络地址与启动步骤见 [本地语音测试](docs/local-testing.md)，与完整上游源码的功能差别见 [功能对比](docs/upstream-voice-comparison.md)。

## 1. 架构与明确不做的事情

服务包含三个独立监听器：`8000` 是语音 WebSocket，`8003` 是 OTA 配置和健康检查，`8004` 是仅供本机使用的记忆管理 API。语音、模型、记忆分别通过 Provider 接口连接；SQLite 保存消息、摘要进度并预留 `relevant_memories`，首版不引入向量数据库。

`listen.start` 需要 `mode`；设备发送的 `listen.stop` 和 `listen.detect` 可以省略它。带文本的 `detect` 直接进入对话和语音回复链路，不调用 ASR。对话文本接收与 TTS 合成通过有界句子队列并行；Edge TTS 当前仍按句收齐音频后转码。

本地测试页支持摄像头 MCP，并通过 `CameraMcpDialogueBackend` 把打开、关闭和拍照工具接入对话。明确的开关、拍照和观察指令直接执行；观察请求会自动开启摄像头、等待画面并拍照。启用视觉配置后，JPEG 交给独立的 `glm-4v-flash` 分析，明确观察请求的视觉描述直接交给 TTS；其他对话仍可通过模型工具循环调用摄像头，并由文字模型组织回复。摄像头属于当前连接的浏览器；使用方法和 `.env` 配置见 [本地语音测试](docs/local-testing.md#摄像头)。`AppConfig` 默认关闭 MCP 和视觉能力，可在本机 `.env` 中启用。

浏览器持续对话使用 Silero VAD 分轮，回复期间检测到连续约 180 ms 语音时自动打断，停止旧播放并接收新一句。采集时请求浏览器回声消除，效果依赖设备和浏览器，可戴耳机测试。此能力在本地测试桥实现；原生 ESP32 连接的回复期间拾音逻辑未改动。

语音 WebSocket 仍不承载通用设备 MCP。服务仍不包含协议 v2/v3、MQTT/UDP、固件包托管、通用 IoT 工具、声纹、多租户、Web 管理台或内置本地 TTS。Edge TTS 需要外网；后文说明如何替换为自己的全本地 `TTSProvider`。

## 2. Windows Python 3.10、Opus DLL 与 FFmpeg

推荐使用 Miniconda/Anaconda PowerShell：

```powershell
conda create -n xiaozhi-voice python=3.10 -y
conda activate xiaozhi-voice
conda install -c conda-forge libopus ffmpeg -y
$env:VOICE_OPUS_DLL_DIR = "$env:CONDA_PREFIX\Library\bin"
ffmpeg -version
```

`VOICE_OPUS_DLL_DIR` 必须指向包含 `opus.dll` 的目录。推荐把实际目录写进 `.env`，启动服务时会在加载 Opus 前读取。也可以写入当前用户环境：

```powershell
[Environment]::SetEnvironmentVariable("VOICE_OPUS_DLL_DIR", "$env:CONDA_PREFIX\Library\bin", "User")
```

FFmpeg 必须在 `PATH` 中，负责把 Edge TTS 或连通性测试的媒体转换为单声道 16-bit PCM。

## 3. 安装依赖

在本目录执行：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env
```

生产环境可以不安装 `requirements-dev.txt`。不要把 `config.yaml`、`.env`、真实 Token 或 API Key 提交到 Git。

### 使用 `.env`

编辑与 `config.yaml` 同目录的 `.env`，可以直接填写模型、密钥和环境路径：

```dotenv
VOICE_LLM_BASE_URL=http://127.0.0.1:11434/v1
VOICE_LLM_MODEL=qwen2.5
VOICE_LLM_API_KEY=
VOICE_TTS_VOICE=zh-CN-XiaoxiaoNeural
# Windows 按实际位置填写：
# VOICE_OPUS_DLL_DIR='D:\Miniconda3\envs\xiaozhi-voice\Library\bin'
```

优先级为 **系统环境变量 > `.env` > `config.yaml` > 默认值**。所有 YAML 配置都支持 `VOICE_<SECTION>_<FIELD>` 形式，例如 `VOICE_SERVER_WS_PORT=8000`、`VOICE_MUSIC_ENABLED=false`。列表使用 YAML/JSON 数组，例如 `VOICE_AUTH_ALLOWED_DEVICES='[device-a, device-b]'`。

兼容原有的 `VOICE_MEMORY_ADMIN_TOKEN`、`NETEASE_API_URL` 和 `FFMPEG` 变量。`FFMPEG` 可指定主服务转码、ASR 检查和音乐网关使用的程序路径。`NETEASE_COOKIE` 也可直接放入 `.env`。独立音乐网关读取 `server/voice/.env`；三个 `check_*` 脚本读取所选配置文件同目录的 `.env`。

值按字面读取，不展开 `$VAR` 或 `${VAR}`；路径请填写实际位置，Windows 路径建议使用单引号。没有 `.env` 时仍可沿用现有配置。修改后重新启动相应进程生效，`.env` 已被 Git 忽略。

在本目录使用 `python run.py --config config.yaml` 启动，无需手动设置 `PYTHONPATH`。原有 `python -m voice_server` 入口仍可使用，但需要先让 Python 找到 `src`。

## 4. 本地 SenseVoice 与 Silero 模型路径

准备离线模型目录，并在 `config.yaml` 中填写相对路径或绝对路径：

```yaml
vad:
  model_path: models/snakers4_silero-vad
asr:
  model_path: models/SenseVoiceSmall
```

Silero 目录内必须存在 `src/silero_vad/data/silero_vad.onnx`。SenseVoice 路径由 FunASR `AutoModel` 直接加载；先在联网机器下载完整模型，再复制整个模型目录，可以避免运行时下载。服务输入固定为 16 kHz、16-bit、单声道 PCM。

## 5. OpenAI 兼容 LLM、智谱与 Ollama

连接远程 OpenAI 兼容服务时，端点和模型可放 YAML 或 `.env`，密钥放 `.env` 或系统环境变量：

本机当前使用智谱，`.env` 对应配置如下；`VOICE_LLM_API_KEY` 填自己的密钥：

```dotenv
VOICE_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
VOICE_LLM_MODEL=glm-4-flash
VOICE_LLM_API_KEY=
```

其他兼容服务示例：

```yaml
llm:
  base_url: https://llm.example.com/v1
  model: your-chat-model
```

```powershell
$env:VOICE_LLM_API_KEY = "<operator-supplied-api-key>"
```

全本地 Ollama 示例：

```powershell
ollama pull qwen2.5
ollama serve
```

```yaml
llm:
  base_url: http://127.0.0.1:11434/v1
  model: qwen2.5
```

本地 Ollama 不需要真实 API Key，代码会使用不具权限意义的本地占位值完成 OpenAI SDK 初始化。

## 6. Edge TTS

```yaml
tts:
  voice: zh-CN-XiaoxiaoNeural
  rate: +0%
  volume: +0%
  timeout_s: 30
```

Edge TTS 会访问微软语音服务。输出先经 FFmpeg 转为 24 kHz、16-bit、单声道 PCM，再按 60 ms 编码为 Opus。可用 `edge-tts --list-voices` 查看当前可用音色。

## 7. 启动与健康检查

### ESP32 局域网歌曲播放

固件保持官方 MQTT 对话服务不变。识别到“播放歌曲/我想听/来一首”等请求后，
会访问固件中配置的电脑音乐网关地址，由下面的轻量网关搜索
网易云音乐并输出 Ogg/Opus 音频。电脑和设备必须连接同一个局域网。

本机当前网关地址应为 `http://192.168.10.111:8010/music?q=...`；修改语音服务 `.env` 不会自动修改固件里已有的网关地址。

先确认 `ffmpeg` 命令可以在 PowerShell 中运行：

```powershell
ffmpeg -version
python .\scripts\music_http_server.py
```

如果系统没有单独安装 FFmpeg，网关会使用 `imageio-ffmpeg` 携带的版本：

```powershell
python -m pip install -r .\requirements-music.txt
python -m pip install imageio-ffmpeg
```

免费歌曲不需要账号。需要会员权限的歌曲，可以在服务端进程启动前设置网易云
Cookie；Cookie 不要提交到 GitHub：

```powershell
$env:NETEASE_COOKIE = "MUSIC_U=你的Cookie; __csrf=你的值"
python .\scripts\music_http_server.py
```

Cookie 只会由服务端访问网易云，ESP32 不会接触账号信息。

浏览器打开 `http://127.0.0.1:8010/health` 应返回 `ok`。若 Windows 防火墙
弹窗，请允许 Python 在专用网络通信；设备访问地址使用电脑局域网 IP，不要使用
`127.0.0.1`。固件中的 `MUSIC_SERVER_URL` 可在 menuconfig 中修改。

先确认 `config.yaml`、`.env`、模型和 Opus DLL，然后启动：

```powershell
python run.py --config config.yaml
```

另开终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8003/health
Invoke-RestMethod -Method Post http://127.0.0.1:8003/xiaozhi/ota/
```

健康响应应为 `{"status":"ok"}`，OTA 响应应包含 `version: 1` 与 `/xiaozhi/v1/` 地址。健康检查只证明 HTTP 进程存活；ASR、LLM、TTS 分别使用第 12 节脚本验证。

## 8. ESP32 OTA 地址与 Windows 防火墙

把 ESP32 的 OTA 地址设为：

```text
http://<电脑局域网IP>:8003/xiaozhi/ota/
```

首次局域网运行保持 `server.host`、`server.ota_host` 为 `0.0.0.0`。以管理员身份只为专用/域网络开放设备所需端口；不要开放 `8004`：

```powershell
New-NetFirewallRule -DisplayName "Xiaozhi Voice WS" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private,Domain -Action Allow
New-NetFirewallRule -DisplayName "Xiaozhi Voice OTA" -Direction Inbound -Protocol TCP -LocalPort 8003 -Profile Private,Domain -Action Allow
```

确认电脑与 ESP32 在同一可信网段，并使用 `ipconfig` 找到电脑的 IPv4 地址。

## 9. 记忆查询、保存与清除

管理服务默认只监听 `127.0.0.1:8004`。建议即使在本机也设置独立管理 Token：

```powershell
$env:VOICE_MEMORY_ADMIN_TOKEN = "<memory_admin_token>"
```

以下命令使用同一个设备 ID；管理 Token 与设备 Bearer Token 不同：

```powershell
curl.exe -H "Authorization: Bearer <memory_admin_token>" "http://127.0.0.1:8004/api/v1/memory/device-a?limit=10"

curl.exe -X POST -H "Authorization: Bearer <memory_admin_token>" -H "Content-Type: application/json" -d '{"session_id":"manual-1","role":"user","content":"我喜欢红茶"}' "http://127.0.0.1:8004/api/v1/memory/device-a/messages"

curl.exe -X DELETE -H "Authorization: Bearer <memory_admin_token>" "http://127.0.0.1:8004/api/v1/memory/device-a"
```

GET 返回摘要、最近消息和当前为空的 `relevant_memories`；POST 只接受 `user`/`assistant`；DELETE 同时删除该设备消息与摘要，不能恢复。

## 10. 切换到全本地 TTS Provider

实现 `src/voice_server/providers/base.py` 中的 `TTSProvider` 契约即可：`synthesize(text)` 必须异步产出 24 kHz、16-bit、单声道、little-endian PCM 字节块，不能直接产出 WAV、MP3 或 Ogg 容器。例如：

```python
class LocalTTSProvider:
    async def synthesize(self, text: str):
        pcm = await local_engine_to_24k_mono_s16le(text)
        for offset in range(0, len(pcm), 2880):
            yield pcm[offset:offset + 2880]
```

然后在 `ServerApplication.from_config()` 中用 `LocalTTSProvider` 替换 `EdgeTTSProvider` 的构造；WebSocket、会话、Opus 和记忆代码不需要修改。首版没有捆绑本地语音模型或额外配置开关，避免把某个引擎硬编码进核心。

## 11. 可信局域网配网与未来公网部署

默认配置 `auth.mode: none` 只适用于可信局域网。上线公网分两个阶段，顺序不能颠倒。

可信局域网配网阶段：

1. 生成强随机设备 Token，放入 `VOICE_AUTH_TOKEN`，把 `auth.mode` 改为 `bearer`。
2. 只在隔离、可信的局域网内临时设置 `auth.ota_include_token: true`，让 ESP32 从 OTA 响应取得并安全保存 Token。
3. 如只需要设备白名单，可使用 `auth.mode: allowlist` 和 `allowed_devices`；它不能替代公网 Bearer Token。当前内置模式互斥，因此公网额外白名单应放在防火墙/反向代理层，或实现同时校验 Token 与 `Device-Id` 的自定义 `Authenticator`。

公网切换阶段：

1. 先把 `auth.ota_include_token` 改回 `false` 并重启；确认公网 OTA 响应绝不含 Token。
2. 配置 `server.public_websocket_url: wss://voice.example.com/xiaozhi/v1/`，并继续使用强 Bearer Token。
3. 将 `server.host`、`server.ota_host`、`admin_api.host` 都绑定到 `127.0.0.1`。使用 [Caddy 示例](deploy/Caddyfile.example) 或 [Nginx 示例](deploy/nginx.conf.example) 在同机终止 TLS；代理必须原样保留 `/xiaozhi/v1/` 和 `/xiaozhi/ota/`。
4. DNS 指向服务器；使用受信 TLS 证书，只允许 `wss://`，不要让 ESP32 忽略证书校验。
5. 云防火墙与 Windows 防火墙只公开 `443`。不公开 `8000`、`8003`、`8004`，也不代理 `/api/`；为已知来源增加 IP 白名单，并启用代理示例中的请求速率、连接数、请求体和超时限制。
6. 每台设备使用独立高熵 Token 更安全；当前配置是单一 Token，如需吊销单台设备，请在公开前实现可插拔的设备凭据存储/`Authenticator`。定期轮换凭据并检查代理日志中没有 Authorization 值。

公网最小安全线是：Bearer Token + 来源白名单、`ota_include_token: false`、WSS/TLS、只开放 443、管理端口永不暴露。Caddy/Nginx 示例都故意不提供 `/api/` 路由。

## 12. 连通性脚本、自动化测试与真机验收

脚本只有在真正执行检查时才加载重模型或访问服务；`--help` 不会加载模型：

```powershell
python scripts/check_asr.py --config config.yaml .\samples\speech.wav
python scripts/check_llm.py --config config.yaml "你好"
python scripts/check_tts.py --config config.yaml "你好" --output data\check-tts.wav
```

ASR 脚本直接读取 16 kHz/16-bit/单声道 PCM WAV；其他 FFmpeg 可读媒体会先转为 16 kHz PCM。TTS 检查文件固定为 24 kHz/16-bit/单声道 WAV。

普通自动化测试不调用真实模型、外网或收费 API：

只运行测试时可安装 `requirements-test.txt`，无需安装 Torch、FunASR、ONNX Runtime 或下载模型；系统仍需安装 libopus。Linux 示例：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
```

```powershell
$env:VOICE_OPUS_DLL_DIR = "$env:CONDA_PREFIX\Library\bin"
python -m pytest -q
python -m compileall -q src scripts
git diff --check
```

有 ESP32、模型和有效服务配置后，再逐项人工完成以下六项；没有硬件时不得把它们标记为已通过：

1. ESP32 OTA 指向 `http://<电脑局域网IP>:8003/xiaozhi/ota/`，成功取得 v1 WebSocket 地址并完成 hello。
2. 连续完成三轮语音对话，第三轮回答能使用前两轮上下文。
3. 播报过程中发送中断，旧音频立即停止，且可以马上开始下一轮。
4. 重启 Server 后，同一设备仍能读到历史消息或摘要。
5. 第二台设备无法读取第一台设备的消息或摘要。
6. 使用第 9 节 API 查询、追加并清除指定设备的记忆，结果与 SQLite 持久化一致。

仓库中的离线集成测试会启动真实 HTTP/WebSocket 监听器，使用真实 SQLite 和 Opus、Fake ASR/LLM/TTS，覆盖 OTA、hello、手动语音流、持久化、设备隔离与 abort；它不能替代以上真机验收。
