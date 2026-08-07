import asyncio
import pytest
from dataclasses import replace

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS, FakeTransport
from tests.session.test_manual_turn import make_session
from test_websocket_server import HELLO, _server
from voice_server.config import AppConfig, ServerConfig
from voice_server.websocket_server import _CloseConnection


async def test_session_pipeline_works_without_asyncio_timeout(monkeypatch):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    transport = FakeTransport()
    session = make_session(
        transport=transport,
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=FakeTTS({"reply.": [b"pcm"]}),
        memory=FakeMemory(),
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm": [b"opus"]}),
    )

    from voice_server.protocol.messages import ListenMessage

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert ("bytes", b"opus") in transport.events


async def test_session_cancellation_still_reaches_provider_without_asyncio_timeout(monkeypatch):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    gate = asyncio.Event()
    asr = FakeASR("hello", gate=gate)
    session = make_session(asr=asr, codec=FakeCodec(decoded=b"pcm-in"))

    from voice_server.protocol.messages import ListenMessage

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    try:
        await asyncio.wait_for(asr.started.wait(), 0.05)
    except TimeoutError:
        pass
    await session.abort()

    assert asr.cancelled.is_set()


async def test_websocket_hello_and_idle_timeout_work_without_asyncio_timeout(monkeypatch):
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    config = replace(AppConfig(), server=ServerConfig(idle_timeout_s=0.001))
    server = _server(config=config)

    class Connection:
        def __init__(self):
            self.request = type("Request", (), {"path": "/xiaozhi/v1/", "headers": {}})()
            self.sent = [HELLO]

        async def recv(self):
            if self.sent:
                return self.sent.pop(0)
            await asyncio.Event().wait()

    connection = Connection()
    hello = await server._receive_hello(connection)
    assert hello.version == 1
    with pytest.raises(_CloseConnection) as error:
        await server._serve_messages(connection, object())
    assert error.value.code == 1000


@pytest.mark.parametrize("path", ["hello", "idle"])
async def test_legacy_asyncio_timeout_is_normalized_to_protocol_close(monkeypatch, path):
    legacy_timeout = type("LegacyTimeoutError", (Exception,), {})

    async def legacy_wait_for(awaitable, timeout):
        awaitable.close()
        raise legacy_timeout("expired")

    monkeypatch.delattr(asyncio, "timeout", raising=False)
    monkeypatch.setattr(asyncio, "TimeoutError", legacy_timeout)
    monkeypatch.setattr(asyncio, "wait_for", legacy_wait_for)
    server = _server(config=replace(AppConfig(), server=ServerConfig()))

    class Connection:
        async def recv(self):
            return HELLO

    connection = Connection()
    with pytest.raises(_CloseConnection) as error:
        if path == "hello":
            await server._receive_hello(connection)
        else:
            await server._serve_messages(connection, object())
    assert error.value.code == (1002 if path == "hello" else 1000)
