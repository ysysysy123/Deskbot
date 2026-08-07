# Voice Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python 3.10 server that speaks the Xiaozhi ESP32 WebSocket v1 protocol and runs a local SenseVoice ASR → OpenAI-compatible LLM → Edge TTS pipeline with device-isolated SQLite memory.

**Architecture:** One asyncio process owns three listeners: WebSocket voice traffic on 8000, OTA configuration HTTP on 8003, and loopback-only memory administration HTTP on 8004. Protocol, session, audio, providers, authentication, and storage communicate through typed interfaces so local LLM/TTS/vector-memory replacements do not change the transport layer.

**Tech Stack:** Python 3.10, asyncio, websockets 14.2, aiohttp 3.13.2, stdlib sqlite3, PyYAML, opuslib-next, FFmpeg, ONNX Runtime/Silero VAD, FunASR SenseVoice, OpenAI Python client, Edge TTS, pytest.

## Global Constraints

- Support only Xiaozhi WebSocket binary protocol v1; a binary frame is one raw Opus packet.
- Input audio is 16 kHz mono Opus; output audio is 24 kHz mono Opus with 60 ms frames.
- Keep one Python process and no Java, Vue, Redis, MySQL, MQTT, UDP, MCP, vector database, or firmware-file hosting.
- Do not copy business implementation from the existing `xiaozhi-esp32-server`; use its protocol documentation only as compatibility evidence.
- Default to LAN operation with voice authentication disabled, OTA on `0.0.0.0:8003`, voice WebSocket on `0.0.0.0:8000`, and memory admin API on `127.0.0.1:8004`.
- Never hard-code secrets, device IDs, absolute model paths, or public domains; secrets come from environment variables.
- `ota_include_token` defaults to false and may be enabled only for first provisioning on a trusted LAN; public OTA responses never reveal a token.
- Store no raw audio by default. Persist only complete user text and completely delivered assistant text.
- Every production behavior follows RED → verify RED → GREEN → verify GREEN. Run the full test suite before every task commit.

## Locked File Map

| Path | Responsibility |
|---|---|
| `src/voice_server/config.py` | Typed YAML/environment configuration and validation |
| `src/voice_server/protocol/messages.py` | Parse client JSON and build server JSON |
| `src/voice_server/protocol/state.py` | Legal session-state transitions |
| `src/voice_server/auth.py` | No-auth, allowlist, Bearer, and admin-token checks |
| `src/voice_server/audio/opus.py` | Stateful input decoder and output encoder |
| `src/voice_server/audio/transcoder.py` | FFmpeg media-to-PCM conversion |
| `src/voice_server/audio/sentences.py` | Streaming LLM sentence segmentation |
| `src/voice_server/providers/base.py` | VAD, ASR, LLM, and TTS contracts |
| `src/voice_server/providers/silero.py` | Per-session Silero ONNX VAD state |
| `src/voice_server/providers/sensevoice.py` | Shared local SenseVoice model with bounded concurrency |
| `src/voice_server/providers/openai_compatible.py` | Async OpenAI-compatible streaming LLM |
| `src/voice_server/providers/edge_tts.py` | Edge audio collection and PCM conversion |
| `src/voice_server/memory/base.py` | Memory and summary-store contracts |
| `src/voice_server/memory/sqlite.py` | SQLite persistence, device isolation, WAL, summary checkpoints |
| `src/voice_server/memory/service.py` | Delegation and tracked asynchronous summary generation |
| `src/voice_server/ota.py` | OTA app and public/local WebSocket URL selection |
| `src/voice_server/admin_api.py` | Token-protected memory CRUD HTTP API |
| `src/voice_server/session.py` | Per-connection recording and response pipeline |
| `src/voice_server/websocket_server.py` | Headers, auth, hello timeout, close codes, routing |
| `src/voice_server/app.py` | Provider construction and listener lifecycle |
| `src/voice_server/__main__.py` | `python -m voice_server` CLI |
| `tests/fakes.py` | Deterministic fake transport and providers |

---

### Task 1: Project foundation and validated configuration

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `src/voice_server/__init__.py`
- Create: `src/voice_server/config.py`
- Create: `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: YAML path and an environment mapping.
- Produces: `load_config(path: Path, environ: Mapping[str, str] = os.environ) -> AppConfig` and frozen dataclasses `ServerConfig`, `AudioConfig`, `AuthConfig`, `AdminApiConfig`, `VadConfig`, `AsrConfig`, `LlmConfig`, `TtsConfig`, `MemoryConfig`.

- [ ] **Step 1: Write failing configuration tests**

```python
from pathlib import Path
import pytest
from voice_server.config import ConfigError, load_config

def test_environment_overrides_llm_secret(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  base_url: http://127.0.0.1:11434/v1\n  model: qwen2.5\n", encoding="utf-8")
    config = load_config(path, {"VOICE_LLM_API_KEY": "secret-value"})
    assert config.llm.api_key == "secret-value"
    assert config.server.ws_port == 8000

def test_non_loopback_admin_requires_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("admin_api:\n  host: 0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="memory admin token"):
        load_config(path, {})

def test_protocol_invariants_cannot_be_changed(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  input_sample_rate: 8000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="16000"):
        load_config(path, {})
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_config.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'voice_server'`.

- [ ] **Step 3: Add dependencies and test discovery**

`requirements.txt` must contain these runtime pins:

```text
aiohttp==3.13.2
websockets==14.2
PyYAML==6.0.3
numpy==1.26.4
onnxruntime>=1.17,<2
opuslib_next==1.1.5
funasr==1.2.7
torch==2.2.2
openai==2.8.1
edge-tts==7.2.6
```

`requirements-dev.txt` must include `-r requirements.txt`, `pytest>=8,<9`, `pytest-asyncio>=0.23,<2`, and `pytest-aiohttp>=1.1,<2`. Configure `pytest.ini` with `pythonpath = src`, `testpaths = tests`, and `asyncio_mode = auto`. Ignore `.venv/`, `__pycache__/`, `.pytest_cache/`, `data/`, `.env`, `*.pyc`, and `.agents/`.

- [ ] **Step 4: Implement the minimum typed loader**

Use frozen dataclasses with the exact defaults from the spec. The critical validation shape is:

```python
class ConfigError(ValueError):
    pass

@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    ws_port: int = 8000
    ota_host: str = "0.0.0.0"
    ota_port: int = 8003
    public_websocket_url: str = ""
    hello_timeout_s: float = 10.0
    idle_timeout_s: float = 120.0
    max_text_bytes: int = 16_384
    max_binary_bytes: int = 65_536
    max_recording_bytes: int = 2_000_000
    max_recording_seconds: float = 30.0

@dataclass(frozen=True)
class AudioConfig:
    input_sample_rate: int = 16_000
    output_sample_rate: int = 24_000
    channels: int = 1
    frame_duration_ms: int = 60

@dataclass(frozen=True)
class AuthConfig:
    mode: str = "none"
    token: str = ""
    allowed_devices: tuple[str, ...] = ()
    ota_include_token: bool = False

@dataclass(frozen=True)
class AdminApiConfig:
    host: str = "127.0.0.1"
    port: int = 8004
    token: str = ""

@dataclass(frozen=True)
class VadConfig:
    model_path: str = "models/snakers4_silero-vad"
    speech_threshold: float = 0.5
    silence_threshold: float = 0.3
    min_silence_duration_ms: int = 600

@dataclass(frozen=True)
class AsrConfig:
    model_path: str = "models/SenseVoiceSmall"
    max_concurrency: int = 1
    timeout_s: float = 30.0

@dataclass(frozen=True)
class LlmConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    summary_max_tokens: int = 256
    timeout_s: float = 60.0

@dataclass(frozen=True)
class TtsConfig:
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    timeout_s: float = 30.0
    error_text: str = "抱歉，服务暂时不可用。"

@dataclass(frozen=True)
class MemoryConfig:
    database_path: str = "data/memory.db"
    recent_limit: int = 10
    summary_threshold: int = 12
```

`AppConfig` owns one instance of every dataclass above through `field(default_factory=...)`. Merge YAML mappings over dataclass defaults, reject unknown keys at every level, then override `VOICE_LLM_API_KEY`, `VOICE_AUTH_TOKEN`, and `VOICE_MEMORY_ADMIN_TOKEN`. Reject ports outside 1–65535, nonpositive timeouts/limits, audio values other than `16000/24000/1/60`, thresholds outside 0–1 or with silence above speech, invalid auth modes, missing allowlist/token values for the selected mode, missing LLM URL/model, nonexistent model paths during real-provider construction, and a non-loopback admin host without a management token.

- [ ] **Step 5: Write `config.example.yaml` with runnable LAN defaults**

Use relative model paths `models/SenseVoiceSmall` and `models/snakers4_silero-vad`, LLM URL `http://127.0.0.1:11434/v1`, model `qwen2.5`, Edge voice `zh-CN-XiaoxiaoNeural`, SQLite path `data/memory.db`, `auth.mode: none`, and `ota_include_token: false`. Do not include secret values.

- [ ] **Step 6: Verify GREEN and commit**

Run: `python -m pytest tests/test_config.py -q`

Expected: all configuration tests pass.

Run: `python -m pytest -q`

Expected: the full suite passes.

```bash
git add .gitignore requirements.txt requirements-dev.txt pytest.ini config.example.yaml src/voice_server tests/test_config.py
git commit -m "feat: add validated server configuration"
```

---

### Task 2: Protocol messages and legal state transitions

**Files:**
- Create: `src/voice_server/protocol/__init__.py`
- Create: `src/voice_server/protocol/messages.py`
- Create: `src/voice_server/protocol/state.py`
- Test: `tests/protocol/test_messages.py`
- Test: `tests/protocol/test_state.py`

**Interfaces:**
- Consumes: raw JSON strings and current `SessionState`.
- Produces: `parse_client_message(raw: str) -> ClientMessage`, `make_server_hello`, `make_stt`, `make_tts`, `make_llm`, and `SessionStateMachine.transition(target)`.

- [ ] **Step 1: Write failing message tests**

```python
import pytest
from voice_server.protocol.messages import (
    AbortMessage, HelloMessage, ListenMessage, ProtocolError,
    make_llm, make_server_hello, parse_client_message,
)

def test_parses_v1_hello():
    message = parse_client_message('{"type":"hello","version":1,"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}')
    assert message == HelloMessage(version=1, format="opus", sample_rate=16000, channels=1, frame_duration=60)

def test_rejects_v2_hello():
    with pytest.raises(ProtocolError) as error:
        parse_client_message('{"type":"hello","version":2,"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}')
    assert error.value.close_code == 1002

def test_parses_listen_and_abort():
    assert parse_client_message('{"type":"listen","state":"start","mode":"manual"}') == ListenMessage(state="start", mode="manual", text=None)
    assert parse_client_message('{"type":"abort"}') == AbortMessage()

def test_builds_server_hello():
    payload = make_server_hello("session-1")
    assert payload["audio_params"] == {"format":"opus", "sample_rate":24000, "channels":1, "frame_duration":60}

def test_builds_display_message_for_esp32():
    assert make_llm("session-1", "🙂", "happy") == {
        "session_id": "session-1", "type": "llm", "text": "🙂", "emotion": "happy"
    }
```

- [ ] **Step 2: Verify message tests fail because the module is absent**

Run: `python -m pytest tests/protocol/test_messages.py -q`

Expected: FAIL during import.

- [ ] **Step 3: Implement immutable message types and strict parsing**

Use frozen dataclasses for `HelloMessage`, `ListenMessage(state, mode, text)`, and `AbortMessage`. Accept listen states `start`, `stop`, and `detect`; accept modes `manual`, `auto`, and `realtime`. `ProtocolError` carries `close_code=1002`. Builders return dictionaries with the session ID and exact protocol fields; `make_tts` accepts states `start`, `sentence_start`, and `stop`, while `make_llm(session_id, text, emotion)` creates the ESP32 display/emotion message.

- [ ] **Step 4: Verify message tests pass**

Run: `python -m pytest tests/protocol/test_messages.py -q`

Expected: all message tests pass.

- [ ] **Step 5: Write failing state-machine tests**

```python
import pytest
from voice_server.protocol.state import InvalidTransition, SessionState, SessionStateMachine

def test_valid_voice_turn_transitions():
    machine = SessionStateMachine()
    for state in (SessionState.IDLE, SessionState.LISTENING, SessionState.RECOGNIZING,
                  SessionState.THINKING, SessionState.SPEAKING, SessionState.IDLE):
        machine.transition(state)
    assert machine.current is SessionState.IDLE

def test_rejects_speaking_before_recognition():
    machine = SessionStateMachine()
    machine.transition(SessionState.IDLE)
    with pytest.raises(InvalidTransition):
        machine.transition(SessionState.SPEAKING)

def test_abort_returns_activity_to_idle():
    machine = SessionStateMachine()
    machine.transition(SessionState.IDLE)
    machine.transition(SessionState.LISTENING)
    machine.abort()
    assert machine.current is SessionState.IDLE
```

- [ ] **Step 6: Verify RED, implement transition table, verify GREEN**

Run: `python -m pytest tests/protocol/test_state.py -q`

Expected: FAIL because `state.py` is absent.

Implement `SessionState` with `CONNECTED`, `IDLE`, `LISTENING`, `RECOGNIZING`, `THINKING`, `SPEAKING`, and `CLOSED`. Encode only the transitions in the approved state diagram; `abort()` maps every nonclosed active state to `IDLE`.

Run: `python -m pytest tests/protocol -q && python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/voice_server/protocol tests/protocol
git commit -m "feat: add xiaozhi v1 protocol model"
```

---

### Task 3: Authentication policies and secret-safe logging

**Files:**
- Create: `src/voice_server/auth.py`
- Create: `src/voice_server/logging_utils.py`
- Test: `tests/test_auth.py`
- Test: `tests/test_logging_utils.py`

**Interfaces:**
- Consumes: `device_id`, Authorization header, `AuthConfig`, and admin token.
- Produces: `Authenticator.authenticate(device_id, authorization) -> bool`, `build_authenticator(config)`, `check_admin_token(header, expected)`, and `redact_secrets(value)`.

- [ ] **Step 1: Write failing authentication tests**

```python
from voice_server.auth import (
    BearerTokenAuthenticator, DeviceAllowlistAuthenticator,
    NoAuthAuthenticator, check_admin_token,
)

def test_no_auth_accepts_device():
    assert NoAuthAuthenticator().authenticate("aa:bb", None)

def test_allowlist_rejects_unknown_device():
    auth = DeviceAllowlistAuthenticator(frozenset({"aa:bb"}))
    assert auth.authenticate("aa:bb", None)
    assert not auth.authenticate("cc:dd", None)

def test_bearer_uses_exact_token():
    auth = BearerTokenAuthenticator("secret")
    assert auth.authenticate("aa:bb", "Bearer secret")
    assert not auth.authenticate("aa:bb", "Bearer secret-x")

def test_admin_token_is_independent():
    assert check_admin_token("Bearer admin-secret", "admin-secret")
    assert not check_admin_token("Bearer device-secret", "admin-secret")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_auth.py -q`

Expected: FAIL because `voice_server.auth` is absent.

- [ ] **Step 3: Implement policies with constant-time comparison**

Use `hmac.compare_digest` for both token checks. Normalize only header scheme casing; do not trim or lowercase token contents. `build_authenticator` maps modes `none`, `allowlist`, and `bearer` and raises `ConfigError` for invalid or incomplete modes.

- [ ] **Step 4: Write and satisfy secret-redaction tests**

```python
from voice_server.logging_utils import redact_secrets

def test_redacts_nested_secret_keys():
    value = {"llm": {"api_key": "abc"}, "Authorization": "Bearer x", "model": "qwen"}
    assert redact_secrets(value) == {"llm": {"api_key": "***"}, "Authorization": "***", "model": "qwen"}
```

Implement recursive mapping/list handling and case-insensitive redaction for `api_key`, `token`, `authorization`, `password`, and `secret`.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/test_auth.py tests/test_logging_utils.py -q && python -m pytest -q`

Expected: all tests pass.

```bash
git add src/voice_server/auth.py src/voice_server/logging_utils.py tests/test_auth.py tests/test_logging_utils.py
git commit -m "feat: add authentication policies"
```

---

### Task 4: Device-isolated SQLite memory provider

**Files:**
- Create: `src/voice_server/memory/__init__.py`
- Create: `src/voice_server/memory/base.py`
- Create: `src/voice_server/memory/models.py`
- Create: `src/voice_server/memory/sqlite.py`
- Test: `tests/memory/test_sqlite.py`

**Interfaces:**
- Consumes: database path, device/session/role/content values.
- Produces: `MemoryMessage`, `MemoryContext`, `SummaryBatch`, `MemoryProvider`, `SummaryStore`, and `SQLiteMemoryProvider.initialize/remember/recall/clear/load_summary_batch/save_summary`.

- [ ] **Step 1: Write failing isolation and persistence tests**

```python
from voice_server.memory.sqlite import SQLiteMemoryProvider

async def test_memory_is_device_isolated_and_persistent(tmp_path):
    path = tmp_path / "memory.db"
    first = SQLiteMemoryProvider(path)
    await first.initialize()
    await first.remember("device-a", "s1", "user", "A remembers red")
    await first.remember("device-b", "s2", "user", "B remembers blue")
    await first.close()

    second = SQLiteMemoryProvider(path)
    await second.initialize()
    a = await second.recall("device-a", "red", 10)
    b = await second.recall("device-b", "blue", 10)
    assert [m.content for m in a.recent_messages] == ["A remembers red"]
    assert [m.content for m in b.recent_messages] == ["B remembers blue"]

async def test_clear_removes_only_target_device(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "one")
    await store.remember("b", "s", "user", "two")
    await store.clear("a")
    assert not (await store.recall("a", "", 10)).recent_messages
    assert (await store.recall("b", "", 10)).recent_messages[0].content == "two"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/memory/test_sqlite.py -q`

Expected: FAIL because memory modules are absent.

- [ ] **Step 3: Implement models, contracts, schema, and CRUD**

Define frozen `MemoryMessage(id, device_id, session_id, role, content, created_at)`, `MemoryContext(summary, recent_messages, relevant_memories)`, and `SummaryBatch(messages, through_message_id, previous_summary)`. Use `asyncio.to_thread` around short stdlib `sqlite3` operations. Open a connection per operation, set `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, use parameterized SQL only, store UTC ISO-8601 timestamps, and order recent rows oldest-to-newest after selecting the newest limit.

The provider contract is:

```python
class MemoryProvider(Protocol):
    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> None: ...
    async def recall(self, device_id: str, query: str, recent_limit: int) -> MemoryContext: ...
    async def clear(self, device_id: str) -> None: ...
    async def close(self) -> None: ...

class SummaryStore(Protocol):
    async def load_summary_batch(self, device_id: str, threshold: int) -> SummaryBatch | None: ...
    async def save_summary(self, device_id: str, summary: str, through_message_id: int) -> None: ...
```

- [ ] **Step 4: Write failing summary-checkpoint tests**

```python
async def test_summary_batch_starts_after_checkpoint(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    for index in range(3):
        await store.remember("a", "s", "user", f"m{index}")
    batch = await store.load_summary_batch("a", threshold=3)
    assert [m.content for m in batch.messages] == ["m0", "m1", "m2"]
    await store.save_summary("a", "summary-1", batch.through_message_id)
    assert await store.load_summary_batch("a", threshold=1) is None
    assert (await store.recall("a", "", 2)).summary == "summary-1"
```

- [ ] **Step 5: Implement transactional summary checkpoint and validate roles/content**

Allow roles only `user` and `assistant`; reject blank device IDs, session IDs, and contents. `clear` must delete messages and summary in one transaction. `save_summary` must update summary text and checkpoint atomically.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest tests/memory/test_sqlite.py -q && python -m pytest -q`

Expected: all tests pass and reopening the database preserves data.

```bash
git add src/voice_server/memory tests/memory
git commit -m "feat: add sqlite conversation memory"
```

---

### Task 5: Provider contracts, fakes, and tracked memory summarization

**Files:**
- Create: `src/voice_server/providers/__init__.py`
- Create: `src/voice_server/providers/base.py`
- Create: `src/voice_server/memory/service.py`
- Create: `tests/fakes.py`
- Test: `tests/memory/test_service.py`

**Interfaces:**
- Consumes: `MemoryProvider`, `SummaryStore`, and `LLMProvider`.
- Produces: provider protocols, reusable fakes, and `MemoryService(provider, store, llm, summary_threshold, summary_max_tokens=256)` with `remember/recall/clear/schedule_summary/close`.

- [ ] **Step 1: Write failing summarization tests**

```python
from voice_server.memory.service import MemoryService
from tests.fakes import FakeLLM

async def test_summary_runs_at_threshold_and_is_saved(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    llm = FakeLLM(["short ", "summary"])
    service = MemoryService(store, store, llm, summary_threshold=2)
    await service.remember("a", "s", "user", "first")
    await service.remember("a", "s", "assistant", "second")
    service.schedule_summary("a")
    await service.close()
    assert (await store.recall("a", "", 10)).summary == "short summary"

async def test_summary_failure_keeps_raw_messages(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    service = MemoryService(store, store, FakeLLM(error=RuntimeError("offline")), 1)
    await service.remember("a", "s", "user", "kept")
    service.schedule_summary("a")
    await service.close()
    assert (await store.recall("a", "", 10)).recent_messages[0].content == "kept"
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/memory/test_service.py -q`

Expected: FAIL because provider contracts and service are absent.

- [ ] **Step 3: Define exact provider contracts and deterministic fakes**

```python
class VADProvider(Protocol):
    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool: ...
class ASRProvider(Protocol):
    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str: ...
class LLMProvider(Protocol):
    def stream(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> AsyncIterator[str]: ...
class TTSProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
```

`tests/fakes.py` must provide `FakeTransport`, `FakeVAD`, `FakeASR`, `FakeLLM`, `FakeTTS`, and `FakeMemory`. Fakes expose received inputs and deterministic outputs; `FakeLLM` records both messages and the optional `max_tokens` override. They never sleep unless a test passes an explicit `asyncio.Event` gate.

- [ ] **Step 4: Implement tracked summary jobs**

`MemoryService.schedule_summary(device_id)` creates at most one task per device and stores it in a set. `_summarize` obtains `SummaryBatch`, sends previous summary plus new messages to the LLM with a fixed Chinese summary instruction, calls `llm.stream(messages, max_tokens=summary_max_tokens)`, joins the async chunks, and commits a nonblank result. Extend the test to assert the configured independent summary limit was recorded. `close()` awaits all tasks with `return_exceptions=True` and then closes the provider. Summary failures are logged and not re-raised into voice sessions.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/memory/test_service.py -q && python -m pytest -q`

Expected: all tests pass.

```bash
git add src/voice_server/providers src/voice_server/memory/service.py tests/fakes.py tests/memory/test_service.py
git commit -m "feat: add provider contracts and memory summaries"
```

---

### Task 6: Opus codec, FFmpeg transcoder, and sentence segmentation

**Files:**
- Create: `src/voice_server/audio/__init__.py`
- Create: `src/voice_server/audio/opus.py`
- Create: `src/voice_server/audio/transcoder.py`
- Create: `src/voice_server/audio/sentences.py`
- Test: `tests/audio/test_opus.py`
- Test: `tests/audio/test_transcoder.py`
- Test: `tests/audio/test_sentences.py`

**Interfaces:**
- Consumes: raw v1 Opus packets, media bytes, and LLM text fragments.
- Produces: `OpusCodec.decode_input(packet) -> bytes`, `OpusCodec.encode_output(pcm) -> list[bytes]`, `FFmpegTranscoder.to_pcm(media) -> bytes`, and `SentenceBuffer.feed/flush`.

- [ ] **Step 1: Write failing real-Opus tests**

```python
import math, struct
import opuslib_next
from voice_server.audio.opus import OpusCodec

def pcm_sine(sample_rate: int, samples: int) -> bytes:
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sample_rate))) for i in range(samples))

def test_decodes_one_v1_input_packet():
    pcm = pcm_sine(16000, 960)
    packet = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO).encode(pcm, 960)
    decoded = OpusCodec().decode_input(packet)
    assert len(decoded) == 960 * 2

def test_encodes_24k_pcm_in_60ms_packets():
    pcm = pcm_sine(24000, 2880)
    packets = OpusCodec().encode_output(pcm)
    assert len(packets) == 2
    decoder = opuslib_next.Decoder(24000, 1)
    assert all(len(decoder.decode(packet, 1440)) == 2880 for packet in packets)
```

- [ ] **Step 2: Verify RED, implement codec, verify GREEN**

Run: `python -m pytest tests/audio/test_opus.py -q`

Expected: FAIL because audio module is absent.

Create one 16 kHz decoder and one 24 kHz encoder per `OpusCodec`. Pad only the final output frame with zero PCM; return no packet for empty input. Convert `opuslib_next.OpusError` into `AudioCodecError` without swallowing it.

Run: `python -m pytest tests/audio/test_opus.py -q`

Expected: both real-library tests pass.

- [ ] **Step 3: Write failing sentence-buffer tests**

```python
from voice_server.audio.sentences import SentenceBuffer

def test_emits_only_complete_sentences_until_flush():
    buffer = SentenceBuffer()
    assert buffer.feed("你好，世界。下一") == ["你好，世界。"]
    assert buffer.feed("句！尾巴") == ["下一句！"]
    assert buffer.flush() == ["尾巴"]
```

Implement punctuation boundaries `。！？.!?\n`, preserve punctuation, ignore blank output, and retain incomplete text until `flush()`.

- [ ] **Step 4: Write failing FFmpeg tests with a fake subprocess**

Test that `to_pcm` invokes `ffmpeg -hide_banner -loglevel error -i pipe:0 -f s16le -ac 1 -ar 24000 pipe:1`, sends the media bytes to stdin, returns stdout, and raises `TranscodeError` containing decoded stderr on nonzero exit. Inject `subprocess_factory` so tests never start FFmpeg.

- [ ] **Step 5: Implement async FFmpeg subprocess handling and verify**

Use `asyncio.create_subprocess_exec` with `PIPE` stdin/stdout/stderr and `await process.communicate(media)`. Do not construct a shell command string.

Run: `python -m pytest tests/audio -q && python -m pytest -q`

Expected: all audio and full-suite tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_server/audio tests/audio
git commit -m "feat: add opus and pcm audio pipeline"
```

---

### Task 7: Local Silero VAD and SenseVoice ASR providers

**Files:**
- Create: `src/voice_server/providers/silero.py`
- Create: `src/voice_server/providers/sensevoice.py`
- Test: `tests/providers/test_silero.py`
- Test: `tests/providers/test_sensevoice.py`

**Interfaces:**
- Consumes: 16 kHz mono PCM, local model paths, and injected model/session objects.
- Produces: per-connection `SileroVADProvider` and shared `SenseVoiceASRProvider`.

- [ ] **Step 1: Write failing Silero tests using a fake ONNX session**

The fake session records `input`, `state`, and `sr`, returns speech probability `0.8`, and returns a state shaped `(2, 1, 128)`. Verify that two provider instances do not share their 512-sample buffer, context, or recurrent state. Verify threshold hysteresis: `>=0.5` is speech, `<=0.3` is silence, and an in-between probability retains the previous result.

- [ ] **Step 2: Verify RED and implement per-session ONNX state**

Run: `python -m pytest tests/providers/test_silero.py -q`

Expected: FAIL because provider is absent.

The constructor accepts `model_path`, thresholds, and optional `inference_session`. If no session is injected, lazily import ONNX Runtime and open `<model_path>/src/silero_vad/data/silero_vad.onnx` with one intra/inter-op thread. Buffer PCM until 512 samples are available, prepend the last 64 float samples as context, and retain recurrent state only on that provider instance.

- [ ] **Step 3: Write failing SenseVoice tests using a fake model**

```python
async def test_sensevoice_transcribes_and_removes_tags():
    model = FakeSenseVoiceModel([{"text": "<|zh|><|NEUTRAL|>你好"}])
    provider = SenseVoiceASRProvider(model=model, max_concurrency=1)
    assert await provider.transcribe(b"\x00\x00" * 1600, 16000) == "你好"
    assert model.generate_kwargs["language"] == "auto"

async def test_sensevoice_rejects_wrong_sample_rate():
    provider = SenseVoiceASRProvider(model=FakeSenseVoiceModel([]), max_concurrency=1)
    with pytest.raises(ValueError, match="16000"):
        await provider.transcribe(b"audio", 8000)
```

- [ ] **Step 4: Implement bounded local inference**

`from_model_path` lazily imports `funasr.AutoModel` and constructs it with `model=path`, `vad_kwargs={"max_single_segment_time": 30000}`, and `disable_update=True`. `transcribe` uses `asyncio.Semaphore(max_concurrency)` and `asyncio.to_thread(model.generate, input=pcm_audio, cache={}, language="auto", use_itn=True, batch_size_s=60)`. Strip `<|...|>` tags and whitespace. Do not retry indefinitely.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/providers/test_silero.py tests/providers/test_sensevoice.py -q && python -m pytest -q`

Expected: provider tests and full suite pass without loading real models.

```bash
git add src/voice_server/providers/silero.py src/voice_server/providers/sensevoice.py tests/providers
git commit -m "feat: add local vad and sensevoice providers"
```

---

### Task 8: OpenAI-compatible LLM and Edge TTS providers

**Files:**
- Create: `src/voice_server/providers/openai_compatible.py`
- Create: `src/voice_server/providers/edge_tts.py`
- Test: `tests/providers/test_openai_compatible.py`
- Test: `tests/providers/test_edge_tts.py`

**Interfaces:**
- Consumes: chat messages, LLM configuration, text, Edge voice, and `FFmpegTranscoder`.
- Produces: async text chunks and 24 kHz mono PCM chunks.

- [ ] **Step 1: Write failing LLM streaming tests**

Create an injected fake `AsyncOpenAI` client whose `chat.completions.create` records `model`, `messages`, `stream=True`, and generation parameters, then returns async chunks containing `"你"`, `"好"`, and one chunk with no content. Assert the provider yields exactly `"你"`, `"好"`, skips missing content, uses API key `local` when the configured key is blank, and propagates cancellation.

- [ ] **Step 2: Verify RED and implement AsyncOpenAI adapter**

Run: `python -m pytest tests/providers/test_openai_compatible.py -q`

Expected: FAIL because provider is absent.

Use `openai.AsyncOpenAI(api_key=api_key or "local", base_url=base_url, timeout=timeout_s)`. Await `chat.completions.create(..., stream=True)` and iterate it asynchronously. Pass the provider's configured `max_tokens` unless the call supplies the summary override from Task 5. Do not catch `asyncio.CancelledError`.

- [ ] **Step 3: Write failing Edge TTS tests**

Inject a fake communicate factory producing metadata, two audio chunks `b"mp3-a"` and `b"mp3-b"`, and a fake transcoder returning `b"pcm"`. Assert the provider passes `b"mp3-amp3-b"` once to the transcoder, yields `b"pcm"`, and passes configured voice/rate/volume to the communicate factory.

- [ ] **Step 4: Implement Edge collection and PCM conversion**

Use `edge_tts.Communicate` only through the injected/default factory. Collect only chunks where `type == "audio"`; reject an empty audio result. Yield the transcoder result in chunks aligned to an even byte count. Do not write temporary audio files.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/providers/test_openai_compatible.py tests/providers/test_edge_tts.py -q && python -m pytest -q`

Expected: all tests pass with no network access.

```bash
git add src/voice_server/providers/openai_compatible.py src/voice_server/providers/edge_tts.py tests/providers
git commit -m "feat: add llm and edge tts providers"
```

---

### Task 9: OTA and memory administration HTTP services

**Files:**
- Create: `src/voice_server/ota.py`
- Create: `src/voice_server/admin_api.py`
- Test: `tests/http/test_ota.py`
- Test: `tests/http/test_admin_api.py`

**Interfaces:**
- Consumes: server/auth/admin configuration and `MemoryService`.
- Produces: `create_ota_app(config) -> aiohttp.web.Application` and `create_admin_app(config, memory) -> web.Application`.

- [ ] **Step 1: Write failing OTA tests with aiohttp TestServer**

Cover GET and POST `/xiaozhi/ota/`, public URL precedence, local URL port replacement, and token provisioning:

```python
from dataclasses import replace
from voice_server.config import AppConfig

def config_with(*, public_websocket_url="", auth_mode="none", auth_token="",
                ota_include_token=False):
    base = AppConfig()
    return replace(
        base,
        server=replace(base.server, public_websocket_url=public_websocket_url),
        auth=replace(base.auth, mode=auth_mode, token=auth_token,
                     ota_include_token=ota_include_token),
    )

async def test_public_ota_omits_token_by_default(aiohttp_client):
    config = config_with(public_websocket_url="wss://voice.example/xiaozhi/v1/",
                         auth_mode="bearer", auth_token="secret", ota_include_token=False)
    client = await aiohttp_client(create_ota_app(config))
    body = await (await client.get("/xiaozhi/ota/")).json()
    assert body == {"websocket": {"url":"wss://voice.example/xiaozhi/v1/", "version":1}}

async def test_trusted_lan_provisioning_can_include_token(aiohttp_client):
    config = config_with(auth_mode="bearer", auth_token="secret", ota_include_token=True)
    client = await aiohttp_client(create_ota_app(config))
    body = await (await client.post("/xiaozhi/ota/", json={"device":"info"})).json()
    assert body["websocket"]["token"] == "secret"

async def test_public_url_never_includes_token_even_if_switch_is_misconfigured(aiohttp_client):
    config = config_with(public_websocket_url="wss://voice.example/xiaozhi/v1/",
                         auth_mode="bearer", auth_token="secret", ota_include_token=True)
    client = await aiohttp_client(create_ota_app(config))
    body = await (await client.get("/xiaozhi/ota/")).json()
    assert "token" not in body["websocket"]
```

- [ ] **Step 2: Verify RED and implement OTA/health routes**

Run: `python -m pytest tests/http/test_ota.py -q`

Expected: FAIL because OTA module is absent.

Add GET/POST `/xiaozhi/ota/` and GET `/health`. Never include a token unless all four are true: auth mode is bearer, token is nonblank, `ota_include_token` is true, and `public_websocket_url` is blank. Use `public_websocket_url` verbatim when configured; otherwise derive `ws://<request-host-without-port>:<ws_port>/xiaozhi/v1/` with IPv6-safe URL parsing.

- [ ] **Step 3: Write failing admin API tests**

Test GET response shape, POST role/content validation and HTTP 201, DELETE HTTP 204, per-device routing, 401 for a missing/wrong `Authorization: Bearer <memory_admin_token>`, and no auth when loopback configuration has an empty admin token. Use `FakeMemory` and assert exact method calls.

- [ ] **Step 4: Implement admin middleware and handlers**

Limit POST content to the configured maximum text bytes, require `session_id`, accept only `user`/`assistant`, and return JSON errors without stack traces. GET accepts `limit` within 1–100. DELETE calls `memory.clear(device_id)` exactly once.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/http -q && python -m pytest -q`

Expected: all HTTP and full-suite tests pass.

```bash
git add src/voice_server/ota.py src/voice_server/admin_api.py tests/http
git commit -m "feat: add ota and memory http services"
```

---

### Task 10: Manual-mode VoiceSession pipeline

**Files:**
- Create: `src/voice_server/session.py`
- Test: `tests/session/test_manual_turn.py`
- Test: `tests/session/test_limits.py`
- Modify: `tests/fakes.py`

**Interfaces:**
- Consumes: parsed messages, v1 Opus packets, transport, codec, providers, memory service, session/device IDs, and limits.
- Produces: `VoiceSession.handle_message`, `handle_audio`, `abort`, `wait_until_idle`, and `close`; test support adds `FakeCodec` and a local `make_session(**overrides)` factory fixed to device `device-a` and session `s1`.

- [ ] **Step 1: Write a failing end-to-end session test with fakes**

Define `make_session(**overrides)` at the top of the test module by constructing `VoiceSession` with deterministic fake defaults, explicit ASR/LLM/TTS timeout values, and the approved byte/time limits. Then add:

```python
async def test_manual_turn_runs_asr_memory_llm_tts_in_order():
    transport = FakeTransport()
    memory = FakeMemory(summary="likes tea", recent=[])
    session = make_session(
        transport=transport,
        asr=FakeASR("你好"),
        llm=FakeLLM(["你好。", "很高兴见到你！"]),
        tts=FakeTTS({"你好。": [b"pcm-a"], "很高兴见到你！": [b"pcm-b"]}),
        memory=memory,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-a":[b"opus-a"], b"pcm-b":[b"opus-b"]}),
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input-opus")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()
    assert transport.events == [
        ("json", {"session_id":"s1", "type":"stt", "text":"你好"}),
        ("json", {"session_id":"s1", "type":"llm", "text":"🙂", "emotion":"happy"}),
        ("json", {"session_id":"s1", "type":"tts", "state":"start"}),
        ("json", {"session_id":"s1", "type":"tts", "state":"sentence_start", "text":"你好。"}),
        ("bytes", b"opus-a"),
        ("json", {"session_id":"s1", "type":"tts", "state":"sentence_start", "text":"很高兴见到你！"}),
        ("bytes", b"opus-b"),
        ("json", {"session_id":"s1", "type":"tts", "state":"stop"}),
    ]
    assert memory.saved == [("device-a","s1","user","你好"), ("device-a","s1","assistant","你好。很高兴见到你！")]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/session/test_manual_turn.py -q`

Expected: FAIL because `VoiceSession` is absent.

- [ ] **Step 3: Implement the minimum manual state flow**

`listen/start` resets buffers and enters `LISTENING`. `handle_audio` rejects packets over `max_binary_bytes`, decodes accepted packets, and appends PCM/byte/time totals. `listen/stop` changes to `RECOGNIZING` and starts one tracked pipeline task. The pipeline must:

1. await ASR with `asyncio.timeout(asr_timeout_s)`;
2. return to idle on blank text;
3. send STT and save the user message;
4. recall memory and build `[system summary, recent messages]` without duplicating the current user row;
5. stream LLM through `SentenceBuffer`;
6. before the first spoken sentence, send one `make_llm(session_id, "🙂", "happy")` display message and TTS start;
7. send sentence start per sentence, encoded bytes, then TTS stop;
8. save assistant text only after all TTS audio is sent;
9. schedule summary and return to idle.

- [ ] **Step 4: Write failing recording-limit tests**

Test that audio before `listen/start` is ignored, a packet above `max_binary_bytes` raises `SessionLimitError(close_code=1009)`, cumulative bytes above `max_recording_bytes` does the same, decoded duration above `max_recording_seconds` does the same, and a second `listen/stop` does not create a second pipeline. Add gated ASR, LLM, and TTS cases that exceed their individual timeout and assert the provider task is cancelled and the session returns to `IDLE`.

- [ ] **Step 5: Implement limits and provider timeout recovery**

Wrap ASR, LLM iteration, and each TTS synthesis in their explicit `asyncio.timeout` values. On ASR/LLM failure or timeout, attempt the configured short error text through TTS. On TTS failure or timeout, send `tts/stop` if start was sent. Await cancelled provider tasks and never retry indefinitely. All error paths return to `IDLE`; no incomplete assistant message is persisted.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest tests/session/test_manual_turn.py tests/session/test_limits.py -q && python -m pytest -q`

Expected: all tests pass.

```bash
git add src/voice_server/session.py tests/session tests/fakes.py
git commit -m "feat: add manual voice session pipeline"
```

---

### Task 11: Auto VAD, abort, cancellation, and session isolation

**Files:**
- Modify: `src/voice_server/session.py`
- Modify: `tests/fakes.py`
- Test: `tests/session/test_auto_vad.py`
- Test: `tests/session/test_abort.py`
- Test: `tests/session/test_isolation.py`

**Interfaces:**
- Consumes: the Task 10 `VoiceSession` API and per-session VAD instance.
- Produces: automatic speech-end submission and cancellation-safe response output.

- [ ] **Step 1: Write failing auto-VAD tests**

Feed one speech frame followed by enough 60 ms silence frames to exceed configured `min_silence_duration_ms`. Assert ASR is invoked once. Feed only silence and assert ASR is never invoked. Assert `manual` mode never submits because of VAD.

- [ ] **Step 2: Verify RED and implement auto submission**

Run: `python -m pytest tests/session/test_auto_vad.py -q`

Expected: FAIL because automatic submission is absent.

Track `heard_speech` and accumulated silence milliseconds in `VoiceSession`; VAD itself only reports speech/non-speech. Reset both on each start/abort. When auto/realtime mode reaches the silence threshold after speech, atomically leave `LISTENING` before creating the pipeline task.

- [ ] **Step 3: Write failing abort tests with gated fakes**

Gate FakeLLM or FakeTTS on an `asyncio.Event`, begin a turn, wait until the provider starts, call `abort`, then release the gate. Assert no bytes arrive after abort, `tts/stop` is sent when needed, incomplete assistant text is absent from memory, user text remains, and the state becomes `IDLE`.

- [ ] **Step 4: Implement cancellation without swallowing `CancelledError`**

Cancel and await the active pipeline task. Increment a per-turn generation number at start and abort; check it before every JSON/binary send. Clear PCM and pending sentence buffers. A new nonmanual `listen/start` invokes abort before opening the next turn; manual start while speaking is ignored.

- [ ] **Step 5: Write and satisfy isolation tests**

Create two sessions with different `device_id`, `OpusCodec`, VAD, transports, and memory outputs. Run both with `asyncio.gather`; assert audio events and saved memory stay on the correct device. This test enforces per-connection codec/VAD state.

- [ ] **Step 6: Verify and commit**

Run: `python -m pytest tests/session -q && python -m pytest -q`

Expected: all session and full-suite tests pass.

```bash
git add src/voice_server/session.py tests/session tests/fakes.py
git commit -m "feat: add automatic listening and interruption"
```

---

### Task 12: WebSocket server and application lifecycle

**Files:**
- Create: `src/voice_server/websocket_server.py`
- Create: `src/voice_server/app.py`
- Create: `src/voice_server/__main__.py`
- Test: `tests/test_websocket_server.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `AppConfig`, `Authenticator`, shared ASR/LLM/TTS/MemoryService, and per-connection codec/VAD factories.
- Produces: `VoiceWebSocketServer.handle_connection`, `ServerApplication.start/stop`, and CLI `main()`.

- [ ] **Step 1: Write failing handshake/close-code tests**

Use a local `websockets.serve` on port 0 and real clients. Cover:

- missing `Device-Id` closes with 1008;
- optional `Client-Id` is accepted and does not replace `Device-Id` as the memory key;
- wrong Bearer closes with 1008;
- first message timeout closes with 1002;
- post-handshake idle timeout closes cleanly without leaking a session;
- v2 hello closes with 1002;
- oversized message closes with 1009;
- valid hello receives exact server hello and can send listen/audio messages.

- [ ] **Step 2: Verify RED and implement the connection boundary**

Run: `python -m pytest tests/test_websocket_server.py -q`

Expected: FAIL because server module is absent.

Validate path `/xiaozhi/v1/`, read case-insensitive request headers, authenticate before business processing, await the first message with `asyncio.timeout(hello_timeout_s)`, require `HelloMessage`, send server hello, then route strings through `parse_client_message` and bytes through `VoiceSession.handle_audio`. Translate `ProtocolError`/`SessionLimitError` to their close codes. Create a new codec and VAD for each connection.

- [ ] **Step 3: Write failing application lifecycle tests**

Inject fake listener factories and providers. Assert `start()` initializes SQLite before accepting connections, starts voice/OTA/admin listeners, and `stop()` closes listeners, active sessions, summary tasks, and providers in reverse order even if one close raises.

- [ ] **Step 4: Implement explicit construction and shutdown**

`ServerApplication.from_config` constructs real providers and service. Do not use a service locator or global mutable providers. `__main__.py` parses `--config` (default `config.yaml`), configures standard logging, runs `asyncio.run`, and exits nonzero for `ConfigError` without printing secrets.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest tests/test_websocket_server.py tests/test_app.py -q && python -m pytest -q`

Expected: all tests pass with no listener leaks.

```bash
git add src/voice_server/websocket_server.py src/voice_server/app.py src/voice_server/__main__.py tests/test_websocket_server.py tests/test_app.py
git commit -m "feat: wire voice server application"
```

---

### Task 13: Offline integration, connectivity checks, and operator documentation

**Files:**
- Create: `tests/integration/test_voice_flow.py`
- Create: `tests/scripts/test_connectivity_cli.py`
- Create: `scripts/check_asr.py`
- Create: `scripts/check_llm.py`
- Create: `scripts/check_tts.py`
- Create: `deploy/Caddyfile.example`
- Create: `deploy/nginx.conf.example`
- Create: `README.md`
- Modify: `config.example.yaml`

**Interfaces:**
- Consumes: all public APIs from Tasks 1–12.
- Produces: one offline real-socket flow, three explicit real-provider checks, and complete operating instructions.

- [ ] **Step 1: Write failing connectivity CLI tests**

```python
import subprocess
import sys
import pytest

@pytest.mark.parametrize("script", ["check_asr.py", "check_llm.py", "check_tts.py"])
def test_connectivity_script_help_does_not_load_provider(script):
    result = subprocess.run(
        [sys.executable, f"scripts/{script}", "--help"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
```

- [ ] **Step 2: Verify CLI tests are RED**

Run: `python -m pytest tests/scripts/test_connectivity_cli.py -q`

Expected: all three parameter cases fail because the script files do not exist.

- [ ] **Step 3: Implement real-provider connectivity scripts and verify GREEN**

Each script accepts `--config`, delays provider imports/construction until after argument parsing, loads the same application config, and exits nonzero with a concise secret-free error on failure:

- `check_asr.py <wav-path>` uses stdlib `wave` for a 16 kHz/16-bit/mono WAV or `FFmpegTranscoder` for other media, then prints SenseVoice text.
- `check_llm.py "你好"` prints streamed LLM output.
- `check_tts.py "你好" --output data/check-tts.wav` writes a 24 kHz/16-bit/mono PCM WAV using Edge TTS output.

Run: `python -m pytest tests/scripts/test_connectivity_cli.py -q`

Expected: all three parameter cases pass without loading models or accessing the network.

- [ ] **Step 4: Add the offline real-listener integration test**

Start a real WebSocket listener and both aiohttp apps on ports supplied by pytest's `unused_tcp_port_factory`, using fake providers and a temporary SQLite file. The test must:

1. POST OTA and assert v1 WebSocket configuration;
2. connect with `Device-Id`;
3. send valid hello and manual listen start;
4. send a real Opus packet and listen stop;
5. assert STT, TTS start/sentence/stop, and at least one binary Opus packet;
6. reconnect after recreating the memory provider and verify saved context persists;
7. connect a second device and verify no first-device content is returned;
8. gate TTS, send `abort`, release the gate, and verify no stale binary frame follows.

- [ ] **Step 5: Run the offline acceptance test**

Run: `python -m pytest tests/integration/test_voice_flow.py -q`

Expected: PASS using only local sockets, fake providers, real SQLite, and the real Opus library. If it fails, use `superpowers:systematic-debugging`, preserve the failing assertion as the regression test, and make the smallest production correction.

- [ ] **Step 6: Add exact reverse-proxy examples**

Caddy must proxy `/xiaozhi/v1/*` to `127.0.0.1:8000` and `/xiaozhi/ota/*` to `127.0.0.1:8003` without stripping either path, omit `/api/`, and rely on automatic TLS. Nginx must include Upgrade/Connection headers, clearly named operator-supplied certificate paths, request/connection limits, and no public 8004 route. Both examples state that `ota_include_token` must be false before exposure.

- [ ] **Step 7: Write the operator README**

Include these sections in order:

1. architecture and explicit non-goals;
2. Windows Python 3.10/Conda, Opus DLL, and FFmpeg setup;
3. dependency installation;
4. local SenseVoice/Silero model paths;
5. OpenAI-compatible and Ollama examples;
6. Edge TTS configuration;
7. start command and health checks;
8. ESP32 OTA URL and Windows Firewall ports;
9. memory GET/POST/DELETE examples with `Authorization: Bearer <memory_admin_token>`;
10. switching to a local TTS Provider;
11. trusted-LAN Token provisioning, disabling `ota_include_token`, WSS proxy, firewall, and public rollout;
12. connectivity scripts, automated tests, and the six real-device acceptance checks from the spec.

- [ ] **Step 8: Run fresh complete verification**

Run: `python -m pytest -q`

Expected: zero failures and zero errors.

Run: `python -m compileall -q src scripts`

Expected: exit code 0 with no output.

Run: `git diff --check`

Expected: exit code 0 with no whitespace errors.

- [ ] **Step 9: Commit**

```bash
git add tests/integration tests/scripts scripts deploy README.md config.example.yaml
git commit -m "docs: add deployment and end-to-end verification"
```

---

## Final Verification Checklist

- [ ] Run `python -m pytest -q` and record the exact passed-test count.
- [ ] Run `python -m compileall -q src scripts` and confirm exit code 0.
- [ ] Run `git diff --check` and confirm exit code 0.
- [ ] Search tracked files for `api_key`, `token`, `authorization`, `password`, and `secret`; confirm only configuration names, redaction tests, or documented environment-variable references exist, with no real values.
- [ ] Compare every item in design sections 2, 3.1, 13, and 14 against a passing automated test or an explicit README manual check.
- [ ] Perform the six real-device acceptance checks when an ESP32 and valid model/API configuration are available; report any unperformed hardware checks as unverified rather than passed.
