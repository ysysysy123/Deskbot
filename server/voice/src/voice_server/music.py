from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


class MusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class MusicTrack:
    title: str
    url: str
    headers: dict[str, str]


class MusicProvider:
    """Searches YouTube and streams a bounded audio preview through FFmpeg."""

    def __init__(self, *, ffmpeg_path: str, max_duration_s: int) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._max_duration_s = max_duration_s

    async def close(self) -> None:
        return None

    async def search(self, query: str) -> MusicTrack | None:
        return await asyncio.to_thread(self._search_sync, query)

    def _search_sync(self, query: str) -> MusicTrack | None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as error:
            raise MusicError("music search requires the yt-dlp package") from error

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "extract_flat": False,
            "format": "bestaudio/best",
        }
        with YoutubeDL(options) as downloader:
            info: Any = downloader.extract_info(f"ytsearch1:{query}", download=False)
        entries = info.get("entries") if isinstance(info, dict) else None
        entry = next((item for item in (entries or ()) if isinstance(item, dict)), None)
        if entry is None or not entry.get("url"):
            return None
        return MusicTrack(
            title=str(entry.get("title") or query).strip(),
            url=str(entry["url"]),
            headers={str(k): str(v) for k, v in (entry.get("http_headers") or {}).items()},
        )

    async def stream_pcm(self, track: MusicTrack):
        command = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            track.url,
            "-vn",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-t",
            str(self._max_duration_s),
            "pipe:1",
        ]
        if track.headers:
            header_text = "".join(f"{key}: {value}\r\n" for key, value in track.headers.items())
            command[1:1] = ["-headers", header_text]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pending = b""
        try:
            while True:
                chunk = await process.stdout.read(16_384)
                if not chunk:
                    break
                pending += chunk
                while len(pending) >= 5_760:
                    yield pending[:5_760]
                    pending = pending[5_760:]
            if pending:
                yield pending
            stderr = await process.stderr.read()
            return_code = await process.wait()
            if return_code:
                raise MusicError(stderr.decode(errors="replace").strip() or "FFmpeg failed to play music")
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
