import runpy
import os
import socket
import sys
from pathlib import Path


def test_search_failure_returns_http_500():
    module = runpy.run_path(str(Path(__file__).parents[2] / "scripts/music_http_server.py"))

    class Provider:
        def _search_sync(self, query):
            raise RuntimeError("music provider unavailable")

    handler = module["MusicHandler"]
    handler.provider = Provider()
    server, client = socket.socketpair()
    try:
        client.sendall(b"GET /music?q=test HTTP/1.0\r\nHost: localhost\r\n\r\n")
        handler(server, ("local", 0), None)
        client.settimeout(1)
        response = client.recv(4096)
        assert response.startswith(b"HTTP/1.0 500")
    finally:
        server.close()
        client.close()


def test_music_gateway_reads_dotenv_before_defaults_and_provider(tmp_path, monkeypatch):
    module = runpy.run_path(str(Path(__file__).parents[2] / "scripts/music_http_server.py"))
    namespace = module["main"].__globals__
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(sys, "argv", ["music_http_server.py"])
    monkeypatch.setitem(namespace, "__file__", str(tmp_path / "scripts/music_http_server.py"))
    (tmp_path / ".env").write_text(
        "MUSIC_HOST=127.0.0.1\nMUSIC_PORT=8120\nNETEASE_COOKIE=example\nFFMPEG=custom-ffmpeg\n",
        encoding="utf-8",
    )
    calls = {}

    class Provider:
        def __init__(self, **kwargs):
            calls.update(kwargs)
            calls["cookie"] = os.environ.get("NETEASE_COOKIE")

    class Server:
        def __init__(self, address, handler):
            calls["address"] = address

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    monkeypatch.setitem(namespace, "MusicProvider", Provider)
    monkeypatch.setitem(namespace, "ThreadingHTTPServer", Server)
    namespace["main"]()
    assert calls["address"] == ("127.0.0.1", 8120)
    assert calls["cookie"] == "example"
    assert calls["ffmpeg_path"] == "custom-ffmpeg"
    assert calls["closed"] is True
