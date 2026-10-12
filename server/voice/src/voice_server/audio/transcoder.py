import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class TranscodeError(RuntimeError):
    pass


async def _kill_and_reap(process: Any) -> None:
    """Kill and reap a child even when the caller is being cancelled."""
    stdin = getattr(process, "stdin", None)
    if stdin is not None and not stdin.is_closing():
        stdin.close()
    if process.returncode is None:
        process.kill()
    for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
        transport = getattr(stream, "_transport", None)
        if transport is not None:
            transport.close()
    # The child watcher updates returncode on a later loop turn.  Let that
    # callback run without awaiting communicate(), whose cancelled readers can
    # otherwise keep the voice turn blocked.
    current = asyncio.current_task()
    waiter = asyncio.create_task(process.wait())
    for _ in range(1000):
        if waiter.done():
            await waiter
            return
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            if current is not None:
                current.uncancel()


class FFmpegTranscoder:
    """Converts media to mono signed 16-bit PCM at the requested sample rate."""

    def __init__(
        self,
        ffmpeg_path: str | None = None,
        *,
        sample_rate: int = 24_000,
        subprocess_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path if ffmpeg_path is not None else os.environ.get("FFMPEG", "ffmpeg")
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
        try:
            stdout, stderr = await process.communicate(media)
        finally:
            if process.returncode is None:
                # ``communicate(media)`` may itself be cancelled when the
                # user interrupts a turn.  Reap the killed child directly;
                # waiting on a second communicate can inherit the cancelled
                # pipe readers and never finish.
                await _kill_and_reap(process)
        if process.returncode:
            raise TranscodeError(stderr.decode(errors="replace"))
        return stdout

    async def stream_mp3_to_pcm(self, media: AsyncIterator[bytes], *, chunk_size: int = 2880) -> AsyncIterator[bytes]:
        """Decode incoming MP3 chunks while PCM is already being played."""
        process = await self._subprocess_factory(
            self._ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "mp3", "-probesize", "32", "-analyzeduration", "0", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self._sample_rate),
            "-flush_packets", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def feed() -> None:
            try:
                async for chunk in media:
                    process.stdin.write(chunk)
                    await process.stdin.drain()
            finally:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        writer = asyncio.create_task(feed())
        errors = asyncio.create_task(process.stderr.read())
        try:
            while True:
                try:
                    yield await process.stdout.readexactly(chunk_size)
                except asyncio.IncompleteReadError as error:
                    if error.partial:
                        yield error.partial
                    break
            # FFmpeg can reject input before its source finishes streaming.
            if not writer.done():
                writer.cancel()
            writer_result = (await asyncio.gather(writer, return_exceptions=True))[0]
            stderr = await errors
            await process.wait()
            if isinstance(writer_result, Exception) and not isinstance(writer_result, (BrokenPipeError, ConnectionResetError)):
                raise writer_result
            if process.returncode:
                raise TranscodeError(stderr.decode(errors="replace"))
        finally:
            writer.cancel()
            errors.cancel()
            await asyncio.gather(writer, errors, return_exceptions=True)
            # Reap the child after closing the writer.  The output/error
            # readers are already cancelled above, so a second communicate
            # would only reintroduce cancellation races during interruption.
            if process.returncode is None:
                await _kill_and_reap(process)
