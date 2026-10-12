import asyncio
from contextlib import aclosing
import os
import shutil
import sys

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


async def test_cancelled_transcode_reaps_its_subprocess():
    started = asyncio.Event()
    process = None

    async def factory(*_args, **kwargs):
        nonlocal process
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(30)", **kwargs
        )
        started.set()
        return process

    task = asyncio.create_task(FFmpegTranscoder(subprocess_factory=factory).to_pcm(b"input"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert process.returncode is not None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()


async def test_ffmpeg_environment_path_is_used_unless_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("FFMPEG", "/custom/ffmpeg")
    commands = []

    async def factory(*args, **kwargs):
        commands.append(args[0])
        return FakeProcess(b"pcm", b"", 0)

    await FFmpegTranscoder(subprocess_factory=factory).to_pcm(b"media")
    await FFmpegTranscoder("explicit-ffmpeg", subprocess_factory=factory).to_pcm(b"media")
    assert commands == ["/custom/ffmpeg", "explicit-ffmpeg"]


@pytest.mark.parametrize("abort_after_output", [False, True])
async def test_stream_abort_closes_source_and_reaps_subprocess(abort_after_output):
    started = asyncio.Event()
    input_closed = asyncio.Event()
    process = None

    async def factory(*_args, **kwargs):
        nonlocal process
        script = "import os,time; os.read(0,4); "
        script += "os.write(1,b'pcm!'); " if abort_after_output else ""
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", script + "time.sleep(30)", **kwargs)
        started.set()
        return process

    async def media():
        try:
            yield b"mp3!"
            await asyncio.Event().wait()
        finally:
            input_closed.set()

    async with aclosing(media()) as source:
        async with aclosing(FFmpegTranscoder(subprocess_factory=factory).stream_mp3_to_pcm(source, chunk_size=4)) as stream:
            task = asyncio.create_task(anext(stream))
            await asyncio.wait_for(started.wait(), 2)
            if abort_after_output:
                assert await asyncio.wait_for(task, 2) == b"pcm!"
                assert not input_closed.is_set()
                await asyncio.wait_for(stream.aclose(), 2)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            assert process.returncode is not None
            assert input_closed.is_set()


async def test_stream_surfaces_input_failure_and_reaps_subprocess():
    process = None

    async def factory(*_args, **kwargs):
        nonlocal process
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import sys; sys.stdin.buffer.read()", **kwargs,
        )
        return process

    async def media():
        yield b"mp3!"
        raise ValueError("TTS connection dropped")

    with pytest.raises(ValueError, match="TTS connection dropped"):
        async for _ in FFmpegTranscoder(subprocess_factory=factory).stream_mp3_to_pcm(media()):
            pass
    assert process.returncode is not None


async def test_real_mp3_stream_yields_pcm_before_input_finishes():
    ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    encoder = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=24000", "-t", "3", "-f", "mp3", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    mp3, stderr = await encoder.communicate()
    assert encoder.returncode == 0, stderr.decode(errors="replace")
    finish = asyncio.Event()
    source_finished = False

    async def media():
        nonlocal source_finished
        halfway = len(mp3) // 2
        yield mp3[:halfway]
        await finish.wait()
        yield mp3[halfway:]
        source_finished = True

    transcoder = FFmpegTranscoder(ffmpeg)
    async with aclosing(media()) as source:
        async with aclosing(transcoder.stream_mp3_to_pcm(source)) as stream:
            first = await asyncio.wait_for(anext(stream), 2)
            assert len(first) == 2880
            assert not source_finished
            finish.set()
            remaining = [chunk async for chunk in stream]
    assert source_finished
    assert first + b"".join(remaining) == await transcoder.to_pcm(mp3)
