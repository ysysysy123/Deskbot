import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence

from voice_server.memory.models import MemoryContext, MemoryMessage


class FakeTransport:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.close_calls: list[tuple[int | None, str | None]] = []

    async def send_json(self, message: dict[str, object]) -> None:
        self.events.append(("json", message))

    async def send_bytes(self, data: bytes) -> None:
        self.events.append(("bytes", data))

    async def close(self, code: int | None = None, message: str | None = None) -> None:
        self.close_calls.append((code, message))


class GatedTransport(FakeTransport):
    def __init__(
        self,
        tts_state: str,
        *,
        record_before_gate: bool,
        swallow_cancel: bool = False,
    ) -> None:
        super().__init__()
        self._tts_state = tts_state
        self._record_before_gate = record_before_gate
        self._swallow_cancel = swallow_cancel
        self._gated = False
        self.gate = asyncio.Event()
        self.send_started = asyncio.Event()
        self.send_cancelled = asyncio.Event()

    async def send_json(self, message: dict[str, object]) -> None:
        should_gate = (
            not self._gated
            and message.get("type") == "tts"
            and message.get("state") == self._tts_state
        )
        if not should_gate:
            await super().send_json(message)
            return
        self._gated = True
        if self._record_before_gate:
            await super().send_json(message)
        self.send_started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.send_cancelled.set()
            if self._swallow_cancel:
                return
            raise
        if not self._record_before_gate:
            await super().send_json(message)


class FailingStopTransport(FakeTransport):
    def __init__(self, stop_failures: int) -> None:
        super().__init__()
        self._stop_failures = stop_failures
        self.stop_attempts = 0

    async def send_json(self, message: dict[str, object]) -> None:
        if message.get("type") == "tts" and message.get("state") == "stop":
            self.stop_attempts += 1
            if self.stop_attempts <= self._stop_failures:
                raise RuntimeError("stop failed")
        await super().send_json(message)


class FakeVAD:
    def __init__(self, speech: bool | Sequence[bool] = False, *, gate: asyncio.Event | None = None) -> None:
        self._speech = [speech] if isinstance(speech, bool) else list(speech)
        self._gate = gate
        self.inputs: list[tuple[bytes, int]] = []
        self.started = asyncio.Event()

    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool:
        self.inputs.append((pcm_chunk, sample_rate))
        self.started.set()
        if self._gate is not None:
            await self._gate.wait()
        if not self._speech:
            return False
        return self._speech.pop(0) if len(self._speech) > 1 else self._speech[0]


class FakeASR:
    def __init__(
        self, text: str = "", *, error: Exception | None = None, gate: asyncio.Event | None = None
    ) -> None:
        self._text = text
        self._error = error
        self._gate = gate
        self.inputs: list[tuple[bytes, int]] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str:
        self.inputs.append((pcm_audio, sample_rate))
        self.started.set()
        try:
            if self._gate is not None:
                await self._gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self._error is not None:
            raise self._error
        return self._text


class FakeLLM:
    def __init__(
        self,
        chunks: Sequence[str] = (),
        *,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._error = error
        self._gate = gate
        self.messages: list[list[dict[str, str]]] = []
        self.max_tokens: list[int | None] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    def stream(
        self, messages: list[dict[str, str]], *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        self.messages.append([dict(message) for message in messages])
        self.max_tokens.append(max_tokens)
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        self.started.set()
        try:
            if self._gate is not None:
                await self._gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self._error is not None:
            raise self._error
        for chunk in self._chunks:
            yield chunk


class FakeTTS:
    def __init__(
        self,
        audio: Mapping[str, Sequence[bytes]] | None = None,
        *,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._audio = {text: list(chunks) for text, chunks in (audio or {}).items()}
        self._error = error
        self._gate = gate
        self.texts: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    def synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        return self._synthesize(text)

    async def _synthesize(self, text: str) -> AsyncIterator[bytes]:
        self.started.set()
        try:
            if self._gate is not None:
                await self._gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        if self._error is not None:
            raise self._error
        for chunk in self._audio.get(text, []):
            yield chunk


class FakeCodec:
    def __init__(
        self,
        *,
        decoded: bytes = b"",
        encoded: Mapping[bytes, Sequence[bytes]] | None = None,
    ) -> None:
        self._decoded = decoded
        self._encoded = {pcm: list(packets) for pcm, packets in (encoded or {}).items()}
        self.inputs: list[bytes] = []
        self.outputs: list[bytes] = []

    def decode_input(self, packet: bytes) -> bytes:
        self.inputs.append(packet)
        return self._decoded

    def encode_output(self, pcm: bytes) -> list[bytes]:
        self.outputs.append(pcm)
        return self._encoded.get(pcm, [])


class FakeMemory:
    def __init__(
        self,
        *,
        summary: str = "",
        recent: Sequence[MemoryMessage] = (),
        relevant: Sequence[MemoryMessage] = (),
    ) -> None:
        self._context = MemoryContext(
            summary=summary,
            recent_messages=list(recent),
            relevant_memories=list(relevant),
        )
        self.saved: list[tuple[str, str, str, str]] = []
        self.recall_calls: list[tuple[str, str, int]] = []
        self.clear_calls: list[str] = []
        self.summary_devices: list[str] = []
        self.close_calls = 0

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> None:
        self.saved.append((device_id, session_id, role, content))

    async def recall(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        self.recall_calls.append((device_id, query, recent_limit))
        return self._context

    async def clear(self, device_id: str) -> None:
        self.clear_calls.append(device_id)

    def schedule_summary(self, device_id: str) -> None:
        self.summary_devices.append(device_id)

    async def close(self) -> None:
        self.close_calls += 1
