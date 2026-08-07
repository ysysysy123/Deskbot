import asyncio
import json
from dataclasses import replace

import pytest
import websockets

from voice_server.auth import BearerTokenAuthenticator, NoAuthAuthenticator
from voice_server.config import AppConfig, ServerConfig
from voice_server.protocol.messages import make_server_hello
from voice_server.websocket_server import VoiceWebSocketServer


HELLO = json.dumps(
    {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
)


class FakeSession:
    def __init__(self, **kwargs):
        self.device_id = kwargs["device_id"]
        self.session_id = kwargs["session_id"]
        self.messages = []
        self.audio = []
        self.closed = False

    async def handle_message(self, message):
        self.messages.append(message)

    async def handle_audio(self, packet):
        self.audio.append(packet)

    async def close(self):
        self.closed = True


def _server(*, config=None, authenticator=None, sessions=None, codecs=None, vads=None):
    sessions = [] if sessions is None else sessions
    codecs = [] if codecs is None else codecs
    vads = [] if vads is None else vads
    return VoiceWebSocketServer(
        config=config or AppConfig(),
        authenticator=authenticator or NoAuthAuthenticator(),
        asr=object(),
        llm=object(),
        tts=object(),
        memory=object(),
        codec_factory=lambda: codecs.append(object()) or codecs[-1],
        vad_factory=lambda: vads.append(object()) or vads[-1],
        session_factory=lambda **kwargs: sessions.append(FakeSession(**kwargs)) or sessions[-1],
        id_factory=lambda: "session-1",
    )


async def _listen(server):
    listener = await websockets.serve(server.handle_connection, "127.0.0.1", 0, max_size=None)
    port = listener.sockets[0].getsockname()[1]
    return listener, f"ws://127.0.0.1:{port}/xiaozhi/v1/"


async def _closed(url, *, headers=None, send=None):
    async with websockets.connect(url, additional_headers=headers or {}) as client:
        if send is not None:
            await client.send(send)
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await client.recv()
        return closed.value.rcvd.code


async def test_missing_device_id_closes_policy_before_factories_run():
    codecs, vads = [], []
    server = _server(codecs=codecs, vads=vads)
    listener, url = await _listen(server)
    try:
        assert await _closed(url) == 1008
        assert codecs == []
        assert vads == []
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.parametrize(
    "headers",
    [
        [("Device-Id", "device-a"), ("device-id", "device-b")],
        [
            ("Device-Id", "device-a"),
            ("Authorization", "Bearer first"),
            ("authorization", "Bearer second"),
        ],
    ],
)
async def test_duplicate_security_headers_close_policy(headers):
    server = _server()
    listener, url = await _listen(server)
    try:
        assert await _closed(url, headers=headers) == 1008
    finally:
        listener.close()
        await listener.wait_closed()


async def test_authenticator_exception_fails_closed_without_handler_error(caplog):
    class BrokenAuthenticator:
        def authenticate(self, device_id, authorization):
            raise RuntimeError("authentication backend unavailable")

    server = _server(authenticator=BrokenAuthenticator())
    listener, url = await _listen(server)
    try:
        assert await _closed(url, headers={"Device-Id": "device-a"}) == 1008
        assert "connection handler failed" not in caplog.text
    finally:
        listener.close()
        await listener.wait_closed()


async def test_client_id_is_accepted_but_device_id_remains_session_memory_key():
    sessions = []
    server = _server(sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a", "Client-Id": "client-b"}) as client:
            await client.send(HELLO)
            assert json.loads(await client.recv()) == make_server_hello("session-1")
            assert sessions[0].device_id == "device-a"
    finally:
        listener.close()
        await listener.wait_closed()


async def test_wrong_bearer_closes_policy_without_exposing_token():
    server = _server(authenticator=BearerTokenAuthenticator("top-secret"))
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a", "Authorization": "Bearer wrong"}) as client:
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await client.recv()
            assert closed.value.rcvd.code == 1008
            assert "top-secret" not in (closed.value.rcvd.reason or "")
    finally:
        listener.close()
        await listener.wait_closed()


async def test_hello_timeout_closes_protocol_error():
    config = replace(AppConfig(), server=ServerConfig(hello_timeout_s=0.01))
    server = _server(config=config)
    listener, url = await _listen(server)
    try:
        assert await _closed(url, headers={"Device-Id": "device-a"}) == 1002
    finally:
        listener.close()
        await listener.wait_closed()


async def test_idle_timeout_closes_session_and_removes_active_session():
    sessions = []
    config = replace(AppConfig(), server=ServerConfig(idle_timeout_s=0.01))
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a"}) as client:
            await client.send(HELLO)
            await client.recv()
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await client.recv()
            assert closed.value.rcvd.code == 1000
        await asyncio.sleep(0.01)
        assert sessions[0].closed
        assert server.active_session_count == 0
    finally:
        listener.close()
        await listener.wait_closed()


async def test_v2_hello_closes_protocol_error():
    server = _server()
    listener, url = await _listen(server)
    try:
        assert await _closed(url, headers={"Device-Id": "device-a"}, send=HELLO.replace('"version": 1', '"version": 2')) == 1002
    finally:
        listener.close()
        await listener.wait_closed()


async def test_post_handshake_hello_closes_protocol_error():
    server = _server()
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a"}) as client:
            await client.send(HELLO)
            await client.recv()
            await client.send(HELLO)
            async with asyncio.timeout(0.1):
                with pytest.raises(websockets.ConnectionClosed) as closed:
                    await client.recv()
            assert closed.value.rcvd.code == 1002
    finally:
        listener.close()
        await listener.wait_closed()


@pytest.mark.parametrize("after_hello", [False, True])
async def test_utf8_oversized_text_closes_message_too_big(after_hello):
    config = replace(AppConfig(), server=ServerConfig(max_text_bytes=2))
    server = _server(config=config)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a"}) as client:
            if after_hello:
                server.config = replace(config, server=replace(config.server, max_text_bytes=1000))
                await client.send(HELLO)
                await client.recv()
                server.config = config
            await client.send(chr(0x4F60))
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await client.recv()
            assert closed.value.rcvd.code == 1009
    finally:
        listener.close()
        await listener.wait_closed()


async def test_valid_hello_routes_listen_and_audio_to_single_session():
    sessions = []
    server = _server(sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"device-id": "device-a"}) as client:
            await client.send(HELLO)
            assert await client.recv() == json.dumps(make_server_hello("session-1"), ensure_ascii=False)
            await client.send('{"type":"listen","state":"start","mode":"manual"}')
            await client.send(b"opus")
            await asyncio.sleep(0.01)
            assert len(sessions) == 1
            assert sessions[0].messages[0].state == "start"
            assert sessions[0].audio == [b"opus"]
    finally:
        listener.close()
        await listener.wait_closed()


async def test_oversized_binary_closes_before_session_handles_packet():
    sessions = []
    config = replace(AppConfig(), server=ServerConfig(max_binary_bytes=3))
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-a"}) as client:
            await client.send(HELLO)
            await client.recv()
            await client.send(b"four")
            async with asyncio.timeout(0.1):
                with pytest.raises(websockets.ConnectionClosed) as closed:
                    await client.recv()
            assert closed.value.rcvd.code == 1009
        assert sessions[0].audio == []
    finally:
        listener.close()
        await listener.wait_closed()


async def test_wrong_path_closes_policy_and_factories_are_per_connection():
    codecs, vads = [], []
    server = _server(codecs=codecs, vads=vads)
    listener, url = await _listen(server)
    try:
        assert await _closed(url.replace("/xiaozhi/v1/", "/wrong"), headers={"Device-Id": "device-a"}) == 1008
        for _ in range(2):
            async with websockets.connect(url, additional_headers={"Device-Id": "device-a"}) as client:
                await client.send(HELLO)
                await client.recv()
        assert len(codecs) == len(vads) == 2
        assert codecs[0] is not codecs[1]
        assert vads[0] is not vads[1]
    finally:
        listener.close()
        await listener.wait_closed()
