from collections.abc import AsyncIterator, Callable
from typing import Any

from voice_server.audio.transcoder import FFmpegTranscoder


class EdgeTTSProvider:
    def __init__(
        self,
        *,
        voice: str,
        rate: str,
        volume: str,
        transcoder: Any | None = None,
        communicate_factory: Callable[..., Any] | None = None,
        chunk_size: int = 2880,
    ) -> None:
        if chunk_size <= 0 or chunk_size % 2:
            raise ValueError("chunk_size must be a positive even number of bytes")
        if communicate_factory is None:
            from edge_tts import Communicate

            communicate_factory = Communicate
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._transcoder = transcoder if transcoder is not None else FFmpegTranscoder()
        self._communicate_factory = communicate_factory
        self._chunk_size = chunk_size

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        communicator = self._communicate_factory(
            text=text,
            voice=self._voice,
            rate=self._rate,
            volume=self._volume,
        )
        audio_parts = []
        async for event in communicator.stream():
            if event.get("type") == "audio":
                audio_parts.append(event["data"])
        media = b"".join(audio_parts)
        if not media:
            raise ValueError("Edge TTS returned no audio")

        pcm = await self._transcoder.to_pcm(media)
        if not pcm:
            raise ValueError("Edge TTS transcoder returned no PCM audio")
        if len(pcm) % 2:
            raise ValueError("Edge TTS PCM must contain an even number of bytes")
        for offset in range(0, len(pcm), self._chunk_size):
            yield pcm[offset : offset + self._chunk_size]
