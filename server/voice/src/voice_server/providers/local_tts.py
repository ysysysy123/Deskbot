from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class LocalTTSError(RuntimeError):
    pass


class LocalCommandTTSProvider:
    """Run a local TTS command that streams raw PCM on stdout.

    The command receives UTF-8 text on stdin and must write 24 kHz, mono,
    little-endian signed 16-bit PCM to stdout.  No shell is involved, so a
    command is parsed once with :func:`shlex.split`.
    """

    def __init__(
        self,
        command: str,
        *,
        chunk_size: int = 2880,
        subprocess_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        if not command.strip():
            raise ValueError("local TTS command must not be blank")
        if chunk_size <= 0 or chunk_size % 2:
            raise ValueError("chunk_size must be a positive even number of bytes")
        self._argv = tuple(shlex.split(command))
        if not self._argv:
            raise ValueError("local TTS command must contain an executable")
        self._chunk_size = chunk_size
        self._subprocess_factory = subprocess_factory

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        process = await self._subprocess_factory(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            process.stdin.write(text.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            wait_closed = getattr(process.stdin, "wait_closed", None)
            if wait_closed is not None:
                try:
                    await wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            received = False
            pending = b""
            while True:
                chunk = await process.stdout.read(self._chunk_size)
                if not chunk:
                    break
                pending += chunk
                complete_size = len(pending) - (len(pending) % 2)
                if complete_size:
                    received = True
                    yield pending[:complete_size]
                    pending = pending[complete_size:]
            if pending:
                raise LocalTTSError("local TTS returned an odd number of PCM bytes")
            stderr = await process.stderr.read()
            return_code = await process.wait()
            if return_code:
                detail = stderr.decode(errors="replace").strip()
                raise LocalTTSError(detail or "local TTS command failed")
            if not received:
                raise LocalTTSError("local TTS returned no audio")
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        finally:
            if getattr(process, "returncode", None) is None:
                await self._terminate(process)

    @staticmethod
    async def _terminate(process: Any) -> None:
        if getattr(process, "returncode", None) is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await process.wait()
        except ProcessLookupError:
            pass
