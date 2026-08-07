import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class TranscodeError(RuntimeError):
    pass


class FFmpegTranscoder:
    """Converts media to mono signed 16-bit PCM at the requested sample rate."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        *,
        sample_rate: int = 24_000,
        subprocess_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._sample_rate = sample_rate
        self._subprocess_factory = subprocess_factory

    async def to_pcm(self, media: bytes) -> bytes:
        process = await self._subprocess_factory(
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(self._sample_rate),
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(media)
        if process.returncode:
            raise TranscodeError(stderr.decode(errors="replace"))
        return stdout
