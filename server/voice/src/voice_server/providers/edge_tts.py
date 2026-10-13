from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
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
            from edge_tts import Communicate, communicate
            from edge_tts.constants import TRUSTED_CLIENT_TOKEN

            # The legacy Bing synthesis endpoint now rejects the v1 client.
            communicate.WSS_URL = (
                "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
                f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}"
            )

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

        async def audio_stream() -> AsyncIterator[bytes]:
            received_audio = False
            async with aclosing(communicator.stream()) as events:
                async for event in events:
                    if event.get("type") == "audio" and event["data"]:
                        received_audio = True
                        yield event["data"]
            if not received_audio:
                raise ValueError("Edge TTS returned no audio")

        received_pcm = False
        async with aclosing(audio_stream()) as media:
            async with aclosing(self._transcoder.stream_mp3_to_pcm(media, chunk_size=self._chunk_size)) as pcm_stream:
                async for pcm in pcm_stream:
                    if len(pcm) % 2:
                        raise ValueError("Edge TTS PCM must contain an even number of bytes")
                    if pcm:
                        received_pcm = True
                        yield pcm
        if not received_pcm:
            raise ValueError("Edge TTS transcoder returned no PCM audio")
