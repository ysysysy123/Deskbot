from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MusicError(RuntimeError):
    pass


@dataclass(frozen=True)
class MusicTrack:
    title: str
    url: str
    headers: dict[str, str]


class MusicProvider:
    """Searches NetEase Cloud Music and streams audio through FFmpeg.

    The device never sees the account cookie. Authentication, song lookup and
    provider-specific playback URLs stay on this server.
    """

    def __init__(
        self,
        *,
        ffmpeg_path: str,
        max_duration_s: int,
        netease_api_url: str = "https://music.163.com",
        netease_cookie: str | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._max_duration_s = max_duration_s
        self._netease_api_url = netease_api_url.rstrip("/")
        self._netease_cookie = netease_cookie or os.environ.get("NETEASE_COOKIE", "")

    async def close(self) -> None:
        return None

    async def search(self, query: str) -> MusicTrack | None:
        return await asyncio.to_thread(self._search_sync, query)

    def _search_sync(self, query: str) -> MusicTrack | None:
        search = self._netease_json(
            "/api/search/get/web",
            {"s": query, "type": 1, "offset": 0, "total": "true", "limit": 5},
        )
        songs = ((search.get("result") or {}).get("songs") or []) if isinstance(search, dict) else []
        for song in songs:
            if not isinstance(song, dict) or not song.get("id"):
                continue
            song_id = int(song["id"])
            playback = self._netease_json(
                "/api/song/enhance/player/url/v1",
                {"ids": json.dumps([song_id], separators=(",", ":")), "level": "standard", "encodeType": "mp3"},
            )
            data = playback.get("data") if isinstance(playback, dict) else None
            item = data[0] if isinstance(data, list) and data else None
            url = item.get("url") if isinstance(item, dict) else None
            if not url:
                continue
            artists = ", ".join(
                str(artist.get("name", "")).strip()
                for artist in (song.get("artists") or [])
                if isinstance(artist, dict) and artist.get("name")
            )
            title = str(song.get("name") or query).strip()
            if artists:
                title = f"{title} - {artists}"
            return MusicTrack(title=title, url=str(url), headers={})
        return None

    def _netease_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params)
        request = Request(
            f"{self._netease_api_url}{path}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Deskbot/1.0",
                **({"Cookie": self._netease_cookie} if self._netease_cookie else {}),
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise MusicError(f"NetEase Music request failed: {error}") from error
        if not isinstance(payload, dict):
            raise MusicError("NetEase Music returned an invalid response")
        return payload

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
