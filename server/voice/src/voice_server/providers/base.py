from collections.abc import AsyncIterator
from typing import Protocol


class VADProvider(Protocol):
    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool: ...


class ASRProvider(Protocol):
    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str: ...


class LLMProvider(Protocol):
    def stream(
        self, messages: list[dict[str, str]], *, max_tokens: int | None = None
    ) -> AsyncIterator[str]: ...


class TTSProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...
