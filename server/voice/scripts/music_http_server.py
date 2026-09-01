"""Small LAN music gateway used by the ESP32 firmware.

The regular voice server is intentionally not required here.  This process
only searches YouTube with yt-dlp and transcodes the selected track to an
Ogg/Opus stream that the firmware already knows how to decode.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from voice_server.music import MusicProvider


LOGGER = logging.getLogger("music-http")


def ffmpeg_command(provider: MusicProvider, track: object) -> list[str]:
    # Keep the command here so this standalone gateway does not need the full
    # voice-server dependency tree.
    url = str(getattr(track, "url"))
    headers = getattr(track, "headers", {})
    command = [
        provider._ffmpeg_path,  # type: ignore[attr-defined]
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if headers:
        header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        command.extend(["-headers", header_text])
    command.extend(
        [
            "-i",
            url,
            "-vn",
            "-t",
            str(provider._max_duration_s),  # type: ignore[attr-defined]
            "-f",
            "ogg",
            "-c:a",
            "libopus",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-application",
            "audio",
            "-frame_duration",
            "60",
            "pipe:1",
        ]
    )
    return command


class MusicHandler(BaseHTTPRequestHandler):
    provider: MusicProvider

    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_text(200, "ok\n")
            return
        if parsed.path != "/music":
            self._send_text(404, "not found\n")
            return

        query = parse_qs(parsed.query).get("q", [""])[0].strip()
        if not query or len(query) > 200:
            self._send_text(400, "missing or invalid q\n")
            return

        process: subprocess.Popen[bytes] | None = None
        try:
            LOGGER.info("searching music: %s", query)
            track = self.provider._search_sync(query)  # type: ignore[attr-defined]
            if track is None:
                self._send_text(404, "music not found\n")
                return

            LOGGER.info("streaming: %s", track.title)
            process = subprocess.Popen(
                ffmpeg_command(self.provider, track),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.send_response(200)
            self.send_header("Content-Type", "audio/ogg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(16_384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            return_code = process.wait(timeout=10)
            if return_code:
                stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
                LOGGER.error("ffmpeg failed: %s", stderr.strip())
        except BrokenPipeError:
            LOGGER.info("ESP32 disconnected while streaming %s", query)
        except Exception:
            LOGGER.exception("music request failed: %s", query)
            if not self.wfile:
                self._send_text(500, "music service failed\n")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    def _send_text(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deskbot LAN music gateway")
    parser.add_argument("--host", default=os.environ.get("MUSIC_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MUSIC_PORT", "8010")))
    parser.add_argument("--ffmpeg", default=os.environ.get("FFMPEG", "ffmpeg"))
    parser.add_argument("--max-duration", type=int, default=300)
    args = parser.parse_args()

    if args.ffmpeg == "ffmpeg" and shutil.which("ffmpeg") is None:
        try:
            import imageio_ffmpeg

            args.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            LOGGER.info("using bundled FFmpeg: %s", args.ffmpeg)
        except ImportError:
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    provider = MusicProvider(ffmpeg_path=args.ffmpeg, max_duration_s=args.max_duration)
    MusicHandler.provider = provider
    server = ThreadingHTTPServer((args.host, args.port), MusicHandler)
    LOGGER.info("music gateway listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("stopping music gateway")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
