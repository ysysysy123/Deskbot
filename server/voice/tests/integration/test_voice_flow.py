import asyncio
import json
from dataclasses import replace

import aiohttp
import websockets

from voice_server.admin_api import create_admin_app
from voice_server.app import ServerApplication, _start_aiohttp
from voice_server.audio.opus import OpusCodec
import opuslib_next
from voice_server.auth import NoAuthAuthenticator
from voice_server.config import AppConfig
from voice_server.memory.service import MemoryService
from voice_server.memory.sqlite import SQLiteMemoryProvider
from voice_server.ota import create_ota_app
from voice_server.websocket_server import VoiceWebSocketServer


HELLO = json.dumps(
    {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 16_000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
)
LISTEN_START = '{"type":"listen","state":"start","mode":"manual"}'
LISTEN_STOP = '{"type":"listen","state":"stop","mode":"manual"}'
ABORT = '{"type":"abort"}'


class FixedASR:
    def __init__(self, text="first-device-memory"):
        self.text = text

    async def transcribe(self, pcm_audio, sample_rate):
        assert pcm_audio
        assert sample_rate == 16_000
        return self.text


class RecordingLLM:
    def __init__(self, response="offline answer。"):
        self.response = response
        self.messages = []

    async def stream(self, messages, *, max_tokens=None):
        self.messages.append([dict(message) for message in messages])
        yield self.response


class GatedPCM:
    def __init__(self):
        self.gate = None
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def synthesize(self, text):
        self.started.set()
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        yield b"\0" * 2_880


class SilentVAD:
    async def is_speech(self, pcm_chunk, sample_rate):
        return False


def _config(database_path, ws_port, ota_port, admin_port):
    base = AppConfig()
    return replace(
        base,
        server=replace(
            base.server,
            host="127.0.0.1",
            ws_port=ws_port,
            ota_host="127.0.0.1",
            ota_port=ota_port,
            idle_timeout_s=2.0,
        ),
        admin_api=replace(base.admin_api, host="127.0.0.1", port=admin_port),
        memory=replace(base.memory, database_path=str(database_path), summary_threshold=100),
    )


def _application(config, asr, llm, tts):
    store = SQLiteMemoryProvider(config.memory.database_path)
    memory = MemoryService(
        store,
        store,
        llm,
        config.memory.summary_threshold,
        config.llm.summary_max_tokens,
    )
    voice = VoiceWebSocketServer(
        config=config,
        authenticator=NoAuthAuthenticator(),
        asr=asr,
        llm=llm,
        tts=tts,
        memory=memory,
        codec_factory=OpusCodec,
        vad_factory=SilentVAD,
    )
    return ServerApplication(
        config=config,
        memory_store=store,
        memory_service=memory,
        websocket_server=voice,
        voice_listener_factory=lambda: websockets.serve(
            voice.handle_connection,
            config.server.host,
            config.server.ws_port,
            max_size=None,
        ),
        ota_listener_factory=lambda: _start_aiohttp(
            create_ota_app(config), config.server.ota_host, config.server.ota_port
        ),
        admin_listener_factory=lambda: _start_aiohttp(
            create_admin_app(config, memory), config.admin_api.host, config.admin_api.port
        ),
    )


def _input_packet():
    encoder = opuslib_next.Encoder(16_000, 1, opuslib_next.APPLICATION_AUDIO)
    return encoder.encode(b"\0" * 1_920, 960)


async def _connect(config, device_id):
    client = await websockets.connect(
        f"ws://127.0.0.1:{config.server.ws_port}/xiaozhi/v1/",
        additional_headers={"Device-Id": device_id},
    )
    await client.send(HELLO)
    hello = json.loads(await client.recv())
    assert hello["type"] == "hello"
    assert hello["version"] == 1
    return client


async def _turn(client):
    await client.send(LISTEN_START)
    await client.send(_input_packet())
    await client.send(LISTEN_STOP)
    events = []
    while True:
        raw = await client.recv()
        events.append(raw if isinstance(raw, bytes) else json.loads(raw))
        if isinstance(events[-1], dict) and events[-1].get("type") == "tts" and events[-1].get("state") == "stop":
            return events


async def test_offline_real_listeners_persist_isolate_and_abort(
    tmp_path,
    unused_tcp_port_factory,
):
    """Would fail if socket wiring, Opus, persistence, isolation, or abort regressed."""
    config = _config(
        tmp_path / "memory.db",
        unused_tcp_port_factory(),
        unused_tcp_port_factory(),
        unused_tcp_port_factory(),
    )

    first_asr = FixedASR()
    first_llm = RecordingLLM()
    first_tts = GatedPCM()
    first_app = _application(config, first_asr, first_llm, first_tts)
    await first_app.start()
    try:
        async with aiohttp.ClientSession() as http:
            response = await http.post(f"http://127.0.0.1:{config.server.ota_port}/xiaozhi/ota/")
            assert await response.json() == {
                "websocket": {
                    "url": f"ws://127.0.0.1:{config.server.ws_port}/xiaozhi/v1/",
                    "version": 1,
                }
            }

        first_client = await _connect(config, "device-a")
        try:
            events = await _turn(first_client)
        finally:
            await first_client.close()
        assert any(isinstance(event, dict) and event.get("type") == "stt" for event in events)
        assert [
            event["state"]
            for event in events
            if isinstance(event, dict) and event.get("type") == "tts"
        ] == ["start", "sentence_start", "stop"]
        assert any(isinstance(event, bytes) and event for event in events)
    finally:
        await first_app.stop()

    second_asr = FixedASR("second-turn")
    second_llm = RecordingLLM()
    second_tts = GatedPCM()
    second_app = _application(config, second_asr, second_llm, second_tts)
    await second_app.start()
    try:
        device_a = await _connect(config, "device-a")
        try:
            await _turn(device_a)
            assert "first-device-memory" in str(second_llm.messages[-1])

            second_asr.text = "isolated-device"
            device_b = await _connect(config, "device-b")
            try:
                await _turn(device_b)
            finally:
                await device_b.close()
            assert "first-device-memory" not in str(second_llm.messages[-1])

            second_asr.text = "abort-this-turn"
            second_tts.started = asyncio.Event()
            second_tts.gate = asyncio.Event()
            await device_a.send(LISTEN_START)
            await device_a.send(_input_packet())
            await device_a.send(LISTEN_STOP)
            await asyncio.wait_for(second_tts.started.wait(), timeout=1)
            await device_a.send(ABORT)
            await asyncio.wait_for(second_tts.cancelled.wait(), timeout=1)
            second_tts.gate.set()

            post_abort = []
            while True:
                try:
                    post_abort.append(await asyncio.wait_for(device_a.recv(), timeout=0.1))
                except TimeoutError:
                    break
            assert not any(isinstance(event, bytes) for event in post_abort)
        finally:
            await device_a.close()
    finally:
        await second_app.stop()
