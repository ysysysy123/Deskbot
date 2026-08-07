import asyncio

import pytest

from voice_server.audio.transcoder import FFmpegTranscoder, TranscodeError


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.input: bytes | None = None

    async def communicate(self, input: bytes) -> tuple[bytes, bytes]:
        self.input = input
        return self._stdout, self._stderr


async def test_to_pcm_passes_media_to_ffmpeg_and_returns_pcm():
    """Would fail if media was not piped through the required 24 kHz PCM command."""
    process = FakeProcess(b"pcm", b"", 0)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def factory(*args: object, **kwargs: object) -> FakeProcess:
        calls.append((args, kwargs))
        return process

    transcoder = FFmpegTranscoder("ffmpeg", subprocess_factory=factory)

    assert await transcoder.to_pcm(b"media") == b"pcm"
    assert process.input == b"media"
    assert calls == [
        (
            (
                "ffmpeg",
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
                "24000",
                "pipe:1",
            ),
            {
                "stdin": asyncio.subprocess.PIPE,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            },
        )
    ]


async def test_to_pcm_raises_transcode_error_with_ffmpeg_stderr():
    """Would fail if failed media conversion hid FFmpeg's diagnostic output."""
    process = FakeProcess(b"", b"invalid media", 1)

    async def factory(*args: object, **kwargs: object) -> FakeProcess:
        return process

    with pytest.raises(TranscodeError, match="invalid media"):
        await FFmpegTranscoder("ffmpeg", subprocess_factory=factory).to_pcm(b"broken")


async def test_to_pcm_can_request_16_khz_for_asr_input():
    """Would fail if the connectivity check sent 24 kHz PCM to SenseVoice."""
    process = FakeProcess(b"pcm", b"", 0)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def factory(*args: object, **kwargs: object) -> FakeProcess:
        calls.append((args, kwargs))
        return process

    transcoder = FFmpegTranscoder(
        "ffmpeg",
        sample_rate=16_000,
        subprocess_factory=factory,
    )

    assert await transcoder.to_pcm(b"media") == b"pcm"
    assert calls[0][0][-2:] == ("16000", "pipe:1")
