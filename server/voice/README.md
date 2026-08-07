# 小智 ESP32 语音 Server

这是一个从零实现的单进程 Python Server，兼容小智 WebSocket 二进制协议 v1：ESP32 上传无容器 Opus，服务端按 `VAD → SenseVoice ASR → SQLite 记忆 → OpenAI 兼容 LLM → Edge TTS → Opus` 处理。默认部署目标是可信局域网，设备以 `Device-Id` 隔离记忆。

## 1. 架构与明确不做的事情

服务包含三个独立监听器：`8000` 是语音 WebSocket，`8003` 是 OTA 配置和健康检查，`8004` 是仅供本机使用的记忆管理 API。语音、模型、记忆分别通过 Provider 接口连接；SQLite 保存消息、摘要进度并预留 `relevant_memories`，首版不引入向量数据库。

首版不支持协议 v2/v3、MQTT/UDP、固件包托管、MCP/IoT 工具、视觉/声纹、多租户、Web 管理台或内置的本地 TTS 引擎。Edge TTS 需要外网；后文说明如何替换为自己的全本地 `TTSProvider`。

## 2. Windows Python 3.10、Opus DLL 与 FFmpeg

推荐使用 Miniconda/Anaconda PowerShell：

```powershell
conda create -n xiaozhi-voice python=3.10 -y
conda activate xiaozhi-voice
conda install -c conda-forge libopus ffmpeg -y
$env:VOICE_OPUS_DLL_DIR = "$env:CONDA_PREFIX\Library\bin"
ffmpeg -version
```

`VOICE_OPUS_DLL_DIR` 必须指向包含 `opus.dll` 的目录。每次打开新终端都要设置；若要写入当前用户环境，可执行：

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
$env:PYTHONPATH = (Resolve-Path .\src).Path
Copy-Item config.example.yaml config.yaml
```

生产环境可以不安装 `requirements-dev.txt`。不要把 `config.yaml`、`.env`、真实 Token 或 API Key 提交到 Git。

## 4. 本地 SenseVoice 与 Silero 模型路径

准备离线模型目录，并在 `config.yaml` 中填写相对路径或绝对路径：

```yaml
vad:
  model_path: models/snakers4_silero-vad
asr:
  model_path: models/SenseVoiceSmall
```

Silero 目录内必须存在 `src/silero_vad/data/silero_vad.onnx`。SenseVoice 路径由 FunASR `AutoModel` 直接加载；先在联网机器下载完整模型，再复制整个模型目录，可以避免运行时下载。服务输入固定为 16 kHz、16-bit、单声道 PCM。

## 5. OpenAI 兼容 LLM 与 Ollama

连接远程 OpenAI 兼容服务时，配置端点和模型，密钥只放环境变量：

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

先确认 `config.yaml`、模型、Opus DLL 和 `PYTHONPATH`，然后启动：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:VOICE_OPUS_DLL_DIR = "$env:CONDA_PREFIX\Library\bin"
python -m voice_server --config config.yaml
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
