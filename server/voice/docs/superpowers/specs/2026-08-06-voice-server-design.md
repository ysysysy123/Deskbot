# Voice Server 设计规格

## 1. 目标

在 `D:\desktop\3dwordmoel\Arduino\voice` 中从零实现一个独立、可理解、可测试的小智 ESP32 语音服务器。第一版直接兼容小智 WebSocket 二进制协议 v1，使用本地 SenseVoice ASR、OpenAI Chat Completions 兼容 LLM、Edge TTS 和 SQLite 记忆。

第一版面向局域网部署，默认关闭设备鉴权，但提供可实际启用的 Bearer Token、设备白名单和管理 Token。系统保留切换到 Ollama、本地 TTS、向量记忆和公网部署的稳定接口，不在第一版引入向量数据库、Java 管理台、Redis、MySQL 或微服务。

## 2. 成功标准

- ESP32 能通过 OTA 配置端点获得 WebSocket v1 地址。
- ESP32 能完成 `hello` 握手、上传 Opus 音频并播放服务器返回的 Opus 音频。
- 一轮语音请求按 VAD、ASR、Memory、LLM、TTS 的顺序完成。
- `abort` 能取消当前 LLM/TTS 任务，旧音频不再继续发送。
- SQLite 能按 `Device-Id` 隔离并持久化消息与摘要。
- 记忆管理 HTTP API 能查询、追加和清除指定设备的数据。
- 自动化测试不依赖真实模型、网络或 API Key，并能离线运行。
- README 覆盖 Windows 安装、模型配置、ESP32 接入、记忆 API、全本地替换和公网部署。

## 3. 范围

### 3.1 第一版包含

- 小智 WebSocket 协议 v1。
- `hello`、`listen`、`abort`、`stt`、`tts` 和 `llm` 消息。
- Opus 上行解码和下行编码。
- Silero VAD、本地 SenseVoice ASR。
- OpenAI 兼容流式 LLM。
- Edge TTS。
- SQLite 消息与摘要记忆。
- OTA 配置服务、健康检查和记忆管理 API。
- 可选设备白名单、Bearer Token 和独立管理 Token。
- Caddy/Nginx 公网部署示例。

### 3.2 第一版不包含

- WebSocket 二进制协议 v2、v3。
- MQTT/UDP 传输。
- 固件文件管理和固件升级包托管。
- MCP、IoT 工具调用、视觉模型和声纹识别。
- 向量数据库和语义相似度检索。
- Java/Vue 管理台、多租户和用户账户系统。
- 内置的完全本地 TTS 实现；只保留并验证 Provider 接口。

## 4. 总体架构

系统是单进程 Python 3.10 应用。HTTP、WebSocket、Provider 和 SQLite 通过清晰接口隔离，避免把传输协议与模型实现耦合。

```text
ESP32
  │
  ├─ HTTP :8003 ──> OTA 配置服务
  │
  └─ WebSocket :8000
         │
         └─ VoiceSession
              ├─ OpusCodec
              ├─ SileroVADProvider
              ├─ SenseVoiceASRProvider
              ├─ SQLiteMemoryProvider
              ├─ OpenAICompatibleLLMProvider
              └─ EdgeTTSProvider

管理程序 ── HTTP :8004 ──> Memory Admin API ──> SQLite
```

建议源码结构：

```text
voice/
├─ src/voice_server/
│  ├─ app.py
│  ├─ config.py
│  ├─ ota.py
│  ├─ auth.py
│  ├─ admin_api.py
│  ├─ session.py
│  ├─ protocol/
│  ├─ audio/
│  ├─ providers/
│  └─ memory/
├─ tests/
├─ docs/
├─ config.example.yaml
├─ requirements.txt
└─ README.md
```

## 5. 服务与端口

| 端口 | 服务 | 默认监听 | 用途 |
|---|---|---|---|
| 8000 | WebSocket | `0.0.0.0` | ESP32 实时语音 |
| 8003 | OTA HTTP | `0.0.0.0` | 向 ESP32 下发 WebSocket 配置 |
| 8004 | 管理 HTTP | `127.0.0.1` | 记忆查询、追加、清除 |

OTA 与管理 API 必须使用独立监听器。ESP32 需要访问 OTA 端口，而记忆管理端点默认只能由本机访问。

OTA 服务同时接受 `GET /xiaozhi/ota/` 和 `POST /xiaozhi/ota/`。POST 请求体可以被忽略，但必须接受 ESP32 固件发送的设备信息。第一版返回固定形状：

```json
{
  "websocket": {
    "url": "ws://192.168.1.10:8000/xiaozhi/v1/",
    "version": 1
  }
}
```

启用 Bearer Token 且 `ota_include_token=true` 时，`websocket` 对象额外返回 `token`。该选项只用于设备首次在可信局域网内配网，默认值为 `false`。ESP32 保存 Token 后，部署者必须在开放公网访问前关闭该选项。配置了 `public_websocket_url` 时，`url` 必须原样使用该公开地址；否则根据 OTA 请求到达的服务器地址生成局域网 URL。

## 6. WebSocket 协议与状态机

### 6.1 连接握手

1. ESP32 连接 `/xiaozhi/v1/`。
2. 请求头必须包含 `Device-Id`，可以包含 `Client-Id` 和 `Authorization`。
3. 启用认证时，在接受业务消息前执行设备白名单或 Bearer Token 校验。
4. 设备必须在 10 秒内发送 `type=hello`。
5. 服务器只接受协议 v1、Opus、16 kHz、单声道输入。
6. 服务器返回 `type=hello`、`transport=websocket`、`session_id` 和 24 kHz、单声道、60 ms 的输出参数。

### 6.2 会话状态

```text
CONNECTED → IDLE → LISTENING → RECOGNIZING → THINKING → SPEAKING → IDLE
任意活动状态 ── abort / disconnect ──> IDLE / CLOSED
```

同一设备连接在同一时间只允许一轮活跃对话。状态转换由 `VoiceSession` 串行管理，Provider 不直接修改会话状态。

### 6.3 录音

- `listen/start` 清空本轮音频缓冲并进入 `LISTENING`。
- v1 二进制帧直接作为原始 Opus 包处理。
- 非 `LISTENING` 状态收到的音频帧被忽略。
- `manual` 模式在收到 `listen/stop` 后提交 ASR。
- `auto` 模式由 Silero VAD 在检测到有效语音后的连续静音时提交 ASR。
- 单次录音具有可配置时长和字节上限。

### 6.4 回答

1. SenseVoice 返回非空文本。
2. 服务器发送 `stt` 消息。
3. Memory 返回设备摘要和最近消息。
4. LLM 流式输出按完整句子切分。
5. 每个完整句子交给 TTS。
6. 服务器依次发送 `tts/start`、`tts/sentence_start`、二进制 Opus 包和 `tts/stop`。
7. 只有完整生成的助手回答被写入记忆。

### 6.5 中断

- 收到 `abort` 时取消当前 LLM 和 TTS 任务并清空未发送音频。
- 新的非手动监听请求可以中断当前播报。
- 已完整识别的用户消息保留。
- 未完整生成或未完整播报的助手回答不进入长期记忆。
- 中断结束后会话回到 `IDLE`，下一轮不复用旧音频缓冲。

## 7. 音频设计

`audio` 模块负责全部编解码，Provider 只处理约定格式的数据。

### 7.1 上行

- 每个 WebSocket 二进制帧是一个无容器的 Opus 包。
- Opus 解码为 16 kHz、16-bit、单声道 PCM。
- PCM 块同时供 VAD 判断和本轮录音缓冲使用。
- ASR 只接收完整的连续 PCM 音频。

### 7.2 下行

- Edge TTS 产生的音频通过 FFmpeg 解码并重采样为 24 kHz、16-bit、单声道 PCM。
- PCM 按 60 ms 分帧，每帧为 1440 个采样点。
- 每帧编码成无容器的原始 Opus 包，通过 WebSocket 二进制帧发送。
- 不把 Ogg、MP3 或 WAV 容器直接发送给 ESP32。

## 8. Provider 接口

```python
class VADProvider:
    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool: ...

class ASRProvider:
    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str: ...

class LLMProvider:
    async def stream(self, messages: list[dict[str, str]]) \
            -> AsyncIterator[str]: ...

class TTSProvider:
    async def synthesize(self, text: str) \
            -> AsyncIterator[bytes]: ...  # 24 kHz mono PCM chunks

class MemoryProvider:
    async def remember(self, device_id: str, session_id: str,
                       role: str, content: str) -> None: ...
    async def recall(self, device_id: str, query: str,
                     recent_limit: int): ...
    async def clear(self, device_id: str) -> None: ...
```

`MemoryProvider.recall` 返回：

```python
@dataclass(frozen=True)
class MemoryContext:
    summary: str
    recent_messages: list[MemoryMessage]
    relevant_memories: list[MemoryMessage]
```

第一版实现：

- `SileroVADProvider`
- `SenseVoiceASRProvider`
- `OpenAICompatibleLLMProvider`
- `EdgeTTSProvider`
- `SQLiteMemoryProvider`

Provider 由配置工厂创建。WebSocket 和 `VoiceSession` 只依赖接口，不依赖具体 Provider 类。

### 8.1 全本地升级路径

- OpenAI 兼容 LLM 的 `base_url` 可以直接指向 Ollama、LM Studio 或 vLLM。
- 本地 TTS 只需实现 `TTSProvider` 并输出统一 PCM/Opus 流。
- 向量记忆只需实现 `MemoryProvider` 并填充 `MemoryContext.relevant_memories`。
- SenseVoice 模型路径由配置提供，可以位于 `voice/models` 或工作区中的已有模型目录。

## 9. SQLite 记忆

### 9.1 数据模型

```text
memory_messages
- id INTEGER PRIMARY KEY
- device_id TEXT NOT NULL
- session_id TEXT NOT NULL
- role TEXT NOT NULL
- content TEXT NOT NULL
- created_at TEXT NOT NULL

memory_summaries
- device_id TEXT PRIMARY KEY
- summary TEXT NOT NULL
- summarized_through_message_id INTEGER NOT NULL
- updated_at TEXT NOT NULL
```

为 `memory_messages(device_id, id)` 建立索引。数据库开启 WAL 和外键约束。

### 9.2 行为

- `Device-Id` 是强制的隔离键。
- 用户消息在 ASR 完整成功后保存。
- 助手消息在 LLM/TTS 流程完整结束后保存。
- `recall` 返回摘要、最近消息和空的 `relevant_memories`。
- 默认每累计 12 条未摘要消息，在会话结束后异步更新摘要。
- 摘要使用同一个 LLM，但使用独立提示词和输出上限。
- 摘要失败不删除或修改原始消息。
- 默认不保存原始音频。

### 9.3 记忆管理 API

```text
GET    /api/v1/memory/{device_id}
POST   /api/v1/memory/{device_id}/messages
DELETE /api/v1/memory/{device_id}
```

- `GET` 返回摘要和最近消息。
- `POST` 接受 `{"role":"user|assistant","content":"...","session_id":"..."}`，并限制内容长度。
- `DELETE` 在一个事务中删除该设备的消息和摘要。
- 管理 API 默认监听 `127.0.0.1:8004`。
- 如果管理 API 监听非回环地址，`memory_admin_token` 必须存在。

`GET` 的成功响应固定为：

```json
{
  "device_id": "设备ID",
  "summary": "摘要文本",
  "recent_messages": [
    {"role": "user", "content": "你好", "session_id": "会话ID", "created_at": "ISO-8601时间"}
  ],
  "relevant_memories": []
}
```

`POST` 成功返回 HTTP 201，`DELETE` 成功返回 HTTP 204。启用管理 Token 时，三个端点都要求 `Authorization: Bearer <memory_admin_token>`。

## 10. 鉴权与安全

语音 WebSocket 支持三种策略：

- `NoAuthAuthenticator`：局域网默认策略。
- `DeviceAllowlistAuthenticator`：只允许配置中的设备 ID。
- `BearerTokenAuthenticator`：验证 `Authorization: Bearer ...`。

记忆管理 API 使用独立管理 Token，不复用设备 Token。配置和日志必须隐藏 API Key、Token、Authorization 和其他秘密值。

设备白名单只提供访问管理便利，不能作为公网强身份认证。公网部署必须使用预先在可信局域网配发并由 ESP32 保存的 Bearer Token；公网 OTA 不得返回 Token。

服务限制包括：

- 最大 WebSocket 文本消息长度。
- 最大单帧二进制长度。
- 最大单轮录音时长和累计字节数。
- hello、空闲、ASR、LLM 和 TTS 超时。
- 每设备单轮并发限制。
- SenseVoice 并发通过可配置 Semaphore 控制，默认值为 1。

## 11. 错误处理

- hello 超时或协议字段非法：使用 WebSocket 关闭码 1002 结束连接。
- 缺少 `Device-Id` 或鉴权失败：使用关闭码 1008 结束连接。
- 消息或音频超过限制：使用关闭码 1009 结束连接并记录原因。
- ASR 返回空文本：结束当前轮，不调用 LLM。
- ASR 或 LLM 失败：尝试用 TTS 播报简短错误提示，然后回到 `IDLE`。
- TTS 失败：发送 `tts/stop`、记录日志并回到 `IDLE`。
- Provider 超时：取消其任务，不执行无限重试。
- `abort`、断连和进程退出：取消相关任务并释放编码器、模型和数据库资源。
- 记忆摘要失败：保留原始消息，不影响语音会话结果。

## 12. 配置

普通配置位于 YAML。秘密配置使用环境变量覆盖。示例配置不包含真实密钥。

主要配置组：

- `server`：监听地址、端口、公开 URL、超时和大小限制。
- `auth`：启用状态、策略、Token 和设备白名单。
- `audio`：输入/输出采样率、帧时长和 Opus 参数。
- `vad`：Silero 模型路径和静音阈值。
- `asr`：SenseVoice 模型路径和并发数。
- `llm`：`base_url`、`model`、API Key 环境变量名和生成参数。
- `tts`：Edge 音色、语速和超时。
- `memory`：SQLite 路径、最近消息数和摘要阈值。
- `admin_api`：绑定地址、端口和管理 Token 环境变量名。

启动时必须校验端口、路径、数值范围和必填字段。错误配置应在监听端口之前终止启动并输出不含秘密值的错误。

## 13. 公网部署路径

首版保持局域网默认，但交付公网部署说明和代理配置示例。

1. 为服务器配置域名和 TLS 证书。
2. 使用 Caddy 或 Nginx 在公网监听 `443`。
3. 将 `/xiaozhi/v1/` 以 WebSocket 方式代理到 `127.0.0.1:8000`。
4. 将 `/xiaozhi/ota/` 代理到 `127.0.0.1:8003`。
5. 设置 `public_websocket_url=wss://域名/xiaozhi/v1/`，让 OTA 返回 WSS 地址。
6. 在可信局域网临时设置 `ota_include_token=true`，让 ESP32 获取并保存 Bearer Token。
7. 配网结束后设置 `ota_include_token=false`，再开放公网访问；公网 OTA 只返回 WSS 地址。
8. 公网启用 Bearer Token。设备白名单不能替代 Token。
9. 只信任显式配置的反向代理地址。
10. 在代理层限制连接速率、请求体和空闲时间。
11. 防火墙只公开 `443`，不直接公开 `8000`、`8003`、`8004`。
12. `/api/` 默认不转发到公网；确需公开时必须启用管理 Token 和独立限流。

## 14. 测试策略

### 14.1 TDD 规则

每项生产行为必须先有失败测试，再写最小实现使测试通过。真实模型、网络和收费服务不参与普通自动化测试。

### 14.2 单元测试

- JSON 消息解析、验证和输出。
- 状态机的正常和非法转换。
- Opus PCM 分帧边界。
- 鉴权策略和秘密脱敏。
- SQLite 保存、查询、清除、摘要进度和设备隔离。
- 配置加载和环境变量覆盖。
- Provider 超时、异常和取消。

### 14.3 本机集成测试

- 启动真实 HTTP 和 WebSocket 监听器。
- Fake 设备通过 OTA 获得地址并完成 hello。
- `listen/start`、音频、`listen/stop` 能产生 `stt`、`tts/start`、二进制音频和 `tts/stop`。
- `abort` 后不再发送旧音频。
- 两台 Fake 设备并发时状态和记忆不串线。
- 服务重启后 SQLite 数据仍存在。
- 使用真实 Opus 库执行一项 PCM 编解码往返测试。

### 14.4 真实设备验收

- ESP32 OTA 地址指向 `http://电脑局域网IP:8003/xiaozhi/ota/`。
- 完成三轮连续语音对话，第三轮能使用前两轮上下文。
- 播报中断后可以立即开始下一轮。
- Server 重启后能读取该设备的历史摘要。
- 第二台设备不能读取第一台设备的记忆。
- 管理 API 能查询、追加和清除指定设备的数据。

## 15. 实现约束

- Python 3.10。
- 首版保持单进程、单仓库和最小依赖。
- 不把现有 `xiaozhi-esp32-server` 的业务实现复制进来。
- 可以使用协议文档和公开消息格式作为兼容依据。
- 不硬编码密钥、设备 ID、模型绝对路径或公网域名。
- 不为首版范围外功能增加抽象或配置。
- 所有对外接口、配置示例和手工验收步骤都写入 README。
