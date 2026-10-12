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
HELLO_MCP = json.dumps(
    {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"mcp": True},
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
        self.device_mcp = kwargs.get("device_mcp")

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
    if config is None:
        base = AppConfig()
        config = replace(base, mcp=replace(base.mcp, enabled=True))
    return VoiceWebSocketServer(
        config=config,
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


async def _reply_mcp(client, request, result):
    await client.send(json.dumps({
        "session_id": "session-1", "type": "mcp",
        "payload": {"jsonrpc": "2.0", "id": request["payload"]["id"], "result": result},
    }))


async def _initialize_device(client, tools):
    initialize = json.loads(await client.recv())
    assert initialize["payload"]["method"] == "initialize"
    await _reply_mcp(client, initialize, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}})
    assert json.loads(await client.recv())["payload"]["method"] == "notifications/initialized"
    request = json.loads(await client.recv())
    assert request["payload"]["method"] == "tools/list"
    await _reply_mcp(client, request, {"tools": tools})


async def test_device_mcp_initializes_paginates_caches_and_reports_tool_results():
    sessions = []
    config = AppConfig()
    config = replace(config, server=replace(config.server, public_websocket_url="ws://192.0.2.10:8000/xiaozhi/v1/"),
                     vision=replace(config.vision, enabled=True),
                     mcp=replace(config.mcp, enabled=True))
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    tool = {"name": "self.sensor.get_imu", "description": "Read IMU", "inputSchema": {"type": "object"}}
    camera = {"name": "self.camera.take_photo", "description": "Take photo"}
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-mcp"}) as client:
            await client.send(HELLO_MCP)
            assert json.loads(await client.recv())["type"] == "hello"
            initialize = json.loads(await client.recv())
            assert initialize["payload"]["method"] == "initialize"
            assert initialize["payload"]["params"]["capabilities"]["vision"] == {
                "url": "http://192.0.2.10:8003/mcp/vision/explain",
            }
            assert server.session_snapshot()[0]["mcp"]["status"] == "initializing"
            await _reply_mcp(client, initialize, {"protocolVersion": "2024-11-05"})
            assert json.loads(await client.recv())["payload"]["method"] == "notifications/initialized"
            first_page = json.loads(await client.recv())
            assert first_page["payload"]["params"] == {"withUserTools": False}
            await _reply_mcp(client, first_page, {"tools": [camera], "nextCursor": tool["name"]})
            second_page = json.loads(await client.recv())
            assert second_page["payload"]["params"]["cursor"] == tool["name"]
            await _reply_mcp(client, second_page, {"tools": [tool]})
            assert await sessions[0].device_mcp.list_tools() == {"tools": [camera, tool]}
            assert await sessions[0].device_mcp.list_tools() == {"tools": [camera, tool]}
            # A second list is served from cache, without another device request.
            await client.send('{"type":"ping","nonce":"cached"}')
            assert json.loads(await client.recv()) == {"type": "pong", "nonce": "cached"}
            snapshot = server.session_snapshot()[0]
            assert snapshot["client_type"] == "device"
            assert snapshot["mcp"]["status"] == "ready"
            assert [item["name"] for item in snapshot["mcp"]["tools"]] == [camera["name"], tool["name"]]

            request_task = asyncio.create_task(server.call_device_tool("session-1", tool["name"], {}))
            outgoing = json.loads(await client.recv())
            assert outgoing["payload"]["params"] == {"name": tool["name"], "arguments": {}}
            assert server.session_snapshot()[0]["mcp"]["last_call"]["status"] == "running"
            result = {"content": [{"type": "text", "text": '{"pitch":1.2}'}], "isError": False}
            await _reply_mcp(client, outgoing, result)
            assert await request_task == result
            last_call = server.session_snapshot()[0]["mcp"]["last_call"]
            assert last_call["status"] == "success"
            assert last_call["summary"] == '{"pitch":1.2}'
            assert last_call["finished_at"] >= last_call["started_at"]
            with pytest.raises(ValueError, match="未提供"):
                await server.call_device_tool("session-1", "self.unknown", {})
    finally:
        listener.close()
        await listener.wait_closed()


async def test_device_mcp_initialization_error_remains_visible_without_breaking_voice():
    sessions = []
    server = _server(sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-mcp"}) as client:
            await client.send(HELLO_MCP)
            await client.recv()
            initialize = json.loads(await client.recv())
            await client.send(json.dumps({"type": "mcp", "payload": {
                "jsonrpc": "2.0", "id": initialize["payload"]["id"],
                "error": {"code": -32601, "message": "initialize unavailable"},
            }}))
            with pytest.raises(ConnectionError, match="initialize unavailable"):
                await sessions[0].device_mcp.list_tools()
            assert server.session_snapshot()[0]["mcp"]["status"] == "error"
            assert server.session_snapshot()[0]["mcp"]["error"] == "initialize unavailable"
            await client.send('{"type":"ping"}')
            assert json.loads(await client.recv()) == {"type": "pong"}
    finally:
        listener.close()
        await listener.wait_closed()


async def test_device_mcp_tool_error_and_disconnect_are_reported():
    sessions = []
    server = _server(sessions=sessions)
    listener, url = await _listen(server)
    tool = {"name": "self.sensor.get_imu"}
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-mcp"}) as client:
            await client.send(HELLO_MCP)
            await client.recv()
            await _initialize_device(client, [tool])
            request_task = asyncio.create_task(server.call_device_tool("session-1", tool["name"], {}))
            outgoing = json.loads(await client.recv())
            result = {"content": [{"type": "text", "text": "IMU unavailable"}], "isError": True}
            await _reply_mcp(client, outgoing, result)
            assert await request_task == result
            snapshot = server.session_snapshot()[0]["mcp"]
            assert snapshot["status"] == "ready"
            assert snapshot["last_call"]["status"] == "error"
            assert snapshot["error"] == "IMU unavailable"
            request_task = asyncio.create_task(server.call_device_tool("session-1", tool["name"], {}))
            await client.recv()
            await client.close()
            with pytest.raises(ConnectionError, match="会话已关闭"):
                await request_task
            assert sessions[0].device_mcp.snapshot()["status"] == "closed"
    finally:
        listener.close()
        await listener.wait_closed()


async def test_device_mcp_discovery_timeout_does_not_repeat_on_every_turn():
    sessions = []
    config = AppConfig()
    config = replace(config, mcp=replace(config.mcp, enabled=True, timeout_seconds=0.01))
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-mcp"}) as client:
            await client.send(HELLO_MCP)
            await client.recv()
            assert json.loads(await client.recv())["payload"]["method"] == "initialize"
            with pytest.raises(ConnectionError, match="初始化超时"):
                await sessions[0].device_mcp.list_tools()
            with pytest.raises(ConnectionError, match="初始化超时"):
                await sessions[0].device_mcp.list_tools()
            await client.send('{"type":"ping"}')
            assert json.loads(await client.recv()) == {"type": "pong"}
    finally:
        listener.close()
        await listener.wait_closed()


async def test_mcp_feature_is_ignored_when_server_mcp_is_disabled():
    base = AppConfig()
    config = replace(base, mcp=replace(base.mcp, enabled=False))
    sessions = []
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    try:
        async with websockets.connect(url, additional_headers={"Device-Id": "device-mcp"}) as client:
            await client.send(HELLO_MCP)
            await client.recv()
            await client.send('{"type":"ping","nonce":"disabled"}')
            assert json.loads(await client.recv()) == {"type": "pong", "nonce": "disabled"}
            assert sessions[0].device_mcp is None
    finally:
        listener.close()
        await listener.wait_closed()


async def test_new_connection_replaces_stale_session_for_same_device():
    base = AppConfig()
    config = replace(base, server=replace(base.server, max_sessions_per_device=1))
    sessions = []
    server = _server(config=config, sessions=sessions)
    listener, url = await _listen(server)
    try:
        first = await websockets.connect(url, additional_headers={"Device-Id": "same-device"})
        await first.send(HELLO)
        await first.recv()
        second = await websockets.connect(url, additional_headers={"Device-Id": "same-device"})
        await second.send(HELLO)
        await second.recv()
        with pytest.raises(websockets.ConnectionClosed):
            await first.recv()
        assert sessions[0].closed is True
        assert server.active_session_count == 1
        await second.close()
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
