import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import aiohttp
import opuslib_next
import pytest
import websockets
from aiohttp import web

from tests.fakes import FakeASR, FakeLLM, FakeMemory, FakeTTS, FakeVAD
from voice_server.audio.opus import OpusCodec
from voice_server.auth import BearerTokenAuthenticator
from voice_server.config import AppConfig
from voice_server.local_testing import BrowserCodec, create_local_test_app
from voice_server.ota import create_ota_app
from voice_server.websocket_server import VoiceWebSocketServer


class LevelVAD:
    """Small deterministic VAD double for bridge tests.

    Production uses the streaming Silero provider; these tests only need to
    exercise bridge segmentation without loading a 2 MB ONNX runtime session.
    """

    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool:
        assert sample_rate == 16_000
        return any(pcm_chunk)


@asynccontextmanager
async def voice_peer(port, *, tts=None, asr=None, llm=None, config=None):
    base = config or AppConfig()
    config = replace(
        base,
        server=replace(base.server, host="127.0.0.1", ws_port=port),
        auth=replace(base.auth, mode="bearer", token="test-device-token"),
    )
    asr = asr or FakeASR("麦克风识别文本")
    llm = llm or FakeLLM(["测试回答。"])
    memory = FakeMemory()
    tts = tts or FakeTTS({"测试回答。": [b"\0" * 2880]})
    voice = VoiceWebSocketServer(
        config=config, authenticator=BearerTokenAuthenticator(config.auth.token),
        asr=asr, llm=llm, tts=tts, memory=memory,
        codec_factory=OpusCodec, vad_factory=FakeVAD,
    )
    async with websockets.serve(voice.handle_connection, "127.0.0.1", port):
        yield config, asr, llm, memory


async def connect(client, **settings):
    ws = await client.ws_connect("/ws")
    await ws.send_json({"type": "connect", "device_id": "browser-test", **settings})
    hello = await ws.receive_json(timeout=2)
    assert hello["type"] == "hello"
    assert hello["audio_params"]["sample_rate"] == 24000
    assert (await ws.receive_json(timeout=2))["type"] == "test.connected"
    return ws


async def read_turn(ws):
    events = []
    while True:
        message = await ws.receive(timeout=2)
        if message.type == aiohttp.WSMsgType.BINARY:
            events.append(message.data)
        else:
            assert message.type == aiohttp.WSMsgType.TEXT
            event = message.json()
            assert event.get("type") != "test.error", event
            events.append(event)
            if event.get("type") == "tts" and event.get("state") == "stop":
                return events


async def read_until(ws, predicate, timeout=2):
    events = []
    while True:
        message = await ws.receive(timeout=timeout)
        if message.type == aiohttp.WSMsgType.BINARY:
            continue
        assert message.type == aiohttp.WSMsgType.TEXT, message
        event = message.json()
        events.append(event)
        if predicate(event):
            return events


async def send_continuous_utterance(ws, *, speech_chunks=1, silence_chunks=6):
    speech = (1000).to_bytes(2, "little", signed=True) * 960
    for _ in range(speech_chunks):
        await ws.send_bytes(speech)
    for _ in range(silence_chunks):
        await ws.send_bytes(b"\0" * 1920)


async def test_browser_text_and_mic_reach_real_voice_protocol(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, asr, llm, memory):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "listen", "state": "detect", "text": "文字测试"})
        events = await read_turn(ws)
        assert not asr.inputs
        assert llm.messages[-1][-1]["content"] == "文字测试"
        assert any(isinstance(event, bytes) and len(event) == 2880 for event in events)

        await ws.send_json({"type": "listen", "state": "start", "mode": "manual"})
        # One full frame and one partial frame must arrive before listen.stop.
        await ws.send_bytes(b"\0" * 1920)
        await ws.send_bytes(b"\0" * 320)
        await ws.send_json({"type": "listen", "state": "stop"})
        events = await read_turn(ws)
        assert len(asr.inputs) == 1
        assert len(asr.inputs[0][0]) == 3840
        assert asr.inputs[0][1] == 16000
        assert any(isinstance(event, dict) and event.get("text") == "麦克风识别文本" for event in events)
        assert {turn[0] for turn in memory.saved} == {"browser-test"}
        await ws.close()


async def test_browser_abort_cancels_real_reply_and_allows_next_turn(aiohttp_client, unused_tcp_port):
    tts = FakeTTS({"测试回答。": [b"\0" * 2880]}, gate=asyncio.Event())
    async with voice_peer(unused_tcp_port, tts=tts) as (config, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "listen", "state": "detect", "text": "打断这一句"})
        await asyncio.wait_for(tts.started.wait(), 2)
        await ws.send_json({"type": "abort"})
        await asyncio.wait_for(tts.cancelled.wait(), 2)
        events = await read_turn(ws)
        assert not any(isinstance(event, bytes) for event in events)
        tts._gate.set()
        await ws.send_json({"type": "listen", "state": "detect", "text": "重新开始"})
        assert any(isinstance(event, bytes) for event in await read_turn(ws))
        await ws.close()


async def test_continuous_microphone_splits_turns_and_rearms(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        started = await ws.receive_json(timeout=2)
        assert started["type"] == "test.continuous" and started["state"] == "started"
        speech = (1000).to_bytes(2, "little", signed=True) * 960
        silence = b"\0" * 1920
        await ws.send_bytes(speech)
        for _ in range(6):
            await ws.send_bytes(silence)
        events = await read_turn(ws)
        assert len(asr.inputs) == 1
        await ws.send_json({"type": "continuous.resume"})
        # Playback is explicitly drained by the browser before the next turn.
        await ws.send_bytes(speech)
        for _ in range(6):
            await ws.send_bytes(silence)
        await read_turn(ws)
        assert len(asr.inputs) == 2
        await ws.send_json({"type": "continuous.stop"})
        stopped = await ws.receive_json(timeout=2)
        while stopped.get("type") != "test.continuous" or stopped.get("state") != "stopped":
            stopped = await ws.receive_json(timeout=2)
        assert stopped["type"] == "test.continuous" and stopped["state"] == "stopped"
        await ws.close()


async def test_continuous_long_silence_does_not_create_asr_turn(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        await ws.receive_json(timeout=2)
        for _ in range(20):
            await ws.send_bytes(b"\0" * 1920)
        await asyncio.sleep(0.05)
        assert asr.inputs == []
        await ws.send_json({"type": "continuous.stop"})
        await read_until(ws, lambda event: event.get("state") == "stopped")
        await ws.close()


async def test_continuous_brief_noise_does_not_interrupt_browser_playback(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        await ws.receive_json(timeout=2)
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("type") == "test.continuous" and event.get("state") == "reply_done")
        assert len(asr.inputs) == 1
        # A brief 60 ms sound must not interrupt the browser's playback queue.
        await send_continuous_utterance(ws)
        await asyncio.sleep(0.05)
        assert len(asr.inputs) == 1
        await ws.send_json({"type": "continuous.resume"})
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("type") == "test.continuous" and event.get("state") == "reply_done")
        assert len(asr.inputs) == 2
        await ws.send_json({"type": "continuous.stop"})
        await read_until(ws, lambda event: event.get("state") == "stopped")
        await ws.close()


async def test_continuous_barge_in_cancels_streaming_reply_and_keeps_new_speech(
    aiohttp_client, unused_tcp_port
):
    class StreamingTTS(FakeTTS):
        async def _synthesize(self, text):
            self.started.set()
            yield b"\0" * 2880
            try:
                await self._gate.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            yield b"\0" * 2880

    tts = StreamingTTS(gate=asyncio.Event())
    async with voice_peer(unused_tcp_port, tts=tts) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        await read_until(ws, lambda event: event.get("state") == "started")
        await send_continuous_utterance(ws)
        while True:
            message = await ws.receive(timeout=2)
            if message.type == aiohttp.WSMsgType.BINARY:
                break
            assert message.type == aiohttp.WSMsgType.TEXT
            assert message.json().get("type") != "test.error"
        assert len(asr.inputs) == 1

        # Silence, 60 ms and 120 ms bursts cannot cancel an active reply.
        await send_continuous_utterance(ws, speech_chunks=0)
        await send_continuous_utterance(ws, speech_chunks=1)
        await send_continuous_utterance(ws, speech_chunks=2)
        await asyncio.sleep(0.05)
        assert not tts.cancelled.is_set()
        assert len(asr.inputs) == 1

        await send_continuous_utterance(ws, speech_chunks=3, silence_chunks=0)
        events = await read_until(ws, lambda event: event.get("state") == "listening")
        assert [event.get("state") for event in events] == ["interrupted", "listening"]
        await asyncio.wait_for(tts.cancelled.wait(), 2)
        tts._gate.set()
        await send_continuous_utterance(ws, speech_chunks=0)
        events = await read_until(ws, lambda event: event.get("state") == "reply_done")
        assert len(asr.inputs) == 2
        # All three onset frames survive the cancellation barrier, followed by
        # five 60 ms silence frames. Old tts.stop/idle cannot end this new turn.
        assert len(asr.inputs[1][0]) == 8 * 1920
        assert events[0]["state"] == "submitted"
        assert sum(event.get("type") == "tts" and event.get("state") == "stop" for event in events) == 1
        await ws.close()


async def test_continuous_barge_in_while_browser_still_plays_finished_reply(
    aiohttp_client, unused_tcp_port
):
    async with voice_peer(unused_tcp_port) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        await read_until(ws, lambda event: event.get("state") == "started")
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("state") == "reply_done")
        # No resume: the server is idle but the browser still has queued audio.
        await send_continuous_utterance(ws, speech_chunks=3)
        events = await read_until(ws, lambda event: event.get("state") == "reply_done")
        assert [event.get("state") for event in events[:3]] == ["interrupted", "listening", "submitted"]
        assert len(asr.inputs) == 2
        await ws.send_json({"type": "continuous.resume"})
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("state") == "reply_done")
        assert len(asr.inputs) == 3
        await ws.close()


async def test_continuous_limit_uses_samples_and_accepts_partial_pcm(aiohttp_client, unused_tcp_port):
    limited = replace(AppConfig(), server=replace(AppConfig().server, max_recording_seconds=0.3))
    async with voice_peer(unused_tcp_port, config=limited) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 3_000, "max_turn_seconds": 30})
        await ws.receive_json(timeout=2)
        # 960 + 1920 + 1920 samples, plus a partial 160 sample chunk.
        await ws.send_bytes((1000).to_bytes(2, "little", signed=True) * 960)
        await ws.send_bytes((1000).to_bytes(2, "little", signed=True) * 1920)
        await ws.send_bytes((1000).to_bytes(2, "little", signed=True) * 1920)
        await ws.send_bytes((1000).to_bytes(2, "little", signed=True) * 160)
        await read_until(ws, lambda event: event.get("type") == "test.continuous" and event.get("state") == "reply_done")
        assert len(asr.inputs) == 1
        assert len(asr.inputs[0][0]) <= 4800 * 2
        await ws.close()


@pytest.mark.parametrize("kind", ["empty_asr", "punctuation_asr", "tts_error"])
async def test_continuous_empty_or_tts_error_emits_reply_done_and_resumes(
    aiohttp_client, unused_tcp_port, kind
):
    asr = FakeASR({"empty_asr": "", "punctuation_asr": ".。…"}.get(kind, "麦克风识别文本"))
    tts = FakeTTS({"测试回答。": [b"\0" * 2880]}, error=RuntimeError("tts failed")) if kind == "tts_error" else None
    async with voice_peer(unused_tcp_port, asr=asr, tts=tts) as (config, asr, llm, memory):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 300})
        await ws.receive_json(timeout=2)
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("type") == "test.continuous" and event.get("state") == "reply_done")
        await ws.send_json({"type": "continuous.resume"})
        await send_continuous_utterance(ws)
        await read_until(ws, lambda event: event.get("type") == "test.continuous" and event.get("state") == "reply_done")
        if kind != "tts_error":
            assert not llm.messages
            assert not memory.saved
        await ws.close()


async def test_continuous_submit_stop_and_short_idle_ping_keep_connection(aiohttp_client, unused_tcp_port):
    base = AppConfig()
    short_idle = replace(base, server=replace(base.server, idle_timeout_s=0.2))
    async with voice_peer(unused_tcp_port, config=short_idle) as (config, asr, *_):
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        ws = await connect(client)
        await ws.send_json({"type": "continuous.start", "silence_ms": 3_000})
        await ws.receive_json(timeout=2)
        # submit explicitly closes the current bounded turn; stop cancels mode.
        await ws.send_bytes((1000).to_bytes(2, "little", signed=True) * 960)
        await ws.send_json({"type": "continuous.submit"})
        await read_until(ws, lambda event: event.get("state") == "submitted")
        await ws.send_json({"type": "continuous.stop"})
        await read_until(ws, lambda event: event.get("state") == "stopped")
        await asyncio.sleep(0.35)
        # No browser input for longer than idle_timeout: the bridge's own ping
        # must have kept the native connection alive (pong is internal).
        await ws.send_json({"type": "continuous.start"})
        restarted = await read_until(ws, lambda event: event.get("state") == "started")
        assert restarted[-1]["type"] == "test.continuous"
        assert asr.inputs
        await ws.close()


async def test_page_info_health_and_auth_failure(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, *_):
        ota = await aiohttp_client(create_ota_app(config))
        model_app = web.Application()
        async def model_list(request):
            return web.json_response({"data": []})

        model_app.router.add_get("/models", model_list)
        models = await aiohttp_client(model_app)
        config = replace(
            config,
            server=replace(config.server, ota_host="127.0.0.1", ota_port=ota.server.port,
                           public_websocket_url="ws://192.168.10.111:8000/xiaozhi/v1/"),
            llm=replace(config.llm, base_url=str(models.make_url("/"))),
        )
        client = await aiohttp_client(create_local_test_app(config, vad_factory=LevelVAD))
        page = await client.get("/")
        assert page.status == 200
        assert "text/html" in page.headers["Content-Type"]
        info = await client.get("/api/info")
        assert config.auth.token not in await info.text()
        assert (await info.json())["lan_ip"] == "192.168.10.111"
        health = await (await client.get("/api/health")).json()
        assert health["voice"]["ok"] and health["ota"]["ok"]
        # Some cloud vendors omit older, still callable models from this list.
        assert health["llm"]["ok"] is None
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "connect", "token": "wrong-token"})
        error = await ws.receive_json(timeout=2)
        assert error["type"] == "test.error"
        assert "wrong-token" not in str(error)
        await ws.close()


async def test_camera_mcp_lists_tools_and_reports_missing_browser(aiohttp_client, unused_tcp_port):
    async with voice_peer(unused_tcp_port) as (config, *_):
        client = await aiohttp_client(create_local_test_app(config))
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status == 200
        listed = await response.json()
        assert "self_camera_take_photo" in [tool["name"] for tool in listed["result"]["tools"]]
        response = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "self_camera_take_photo", "arguments": {}},
        })
        result = await response.json()
        assert result["result"]["isError"] is True
        response = await client.get("/ws", headers={"Origin": "https://unrelated.example"})
        assert response.status == 403


def test_browser_pcm_frames_match_device_opus():
    codec = BrowserCodec()
    assert codec.encode(b"\0" * 320) == []
    packets = codec.encode(b"", flush=True)
    assert len(packets) == 1
    device = OpusCodec()
    assert len(device.decode_input(packets[0])) == 1920
    encoder = opuslib_next.Encoder(24000, 1, opuslib_next.APPLICATION_AUDIO)
    assert len(codec.decode(encoder.encode(b"\0" * 2880, 1440))) == 2880
