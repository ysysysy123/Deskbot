"""Local browser test bridge: PCM in the browser, native v1 Opus on the wire."""

import asyncio
import importlib.util
import json
import math
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from voice_server.config import AppConfig
from voice_server.compat import wait_for
from voice_server.camera_mcp import CameraMCPBridge
from voice_server.protocol.messages import AbortMessage, ListenMessage, parse_client_message
from voice_server.providers.silero import SileroVADProvider


HELLO = {
    "type": "hello", "version": 1, "transport": "websocket",
    "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
}


def _local_host(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return f"[{host}]" if ":" in host else host


class BrowserCodec:
    def __init__(self) -> None:
        # Apply the existing Windows DLL setup, after load_config has read .env.
        from voice_server.audio.opus import opuslib_next

        self.encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)
        self.decoder = opuslib_next.Decoder(24000, 1)
        self.pending = bytearray()

    def encode(self, pcm: bytes, *, flush: bool = False) -> list[bytes]:
        if len(pcm) % 2:
            raise ValueError("PCM must contain complete int16 samples")
        self.pending.extend(pcm)
        if flush and self.pending:
            self.pending.extend(b"\0" * ((-len(self.pending)) % 1920))
        packets = []
        while len(self.pending) >= 1920:
            packets.append(self.encoder.encode(bytes(self.pending[:1920]), 960))
            del self.pending[:1920]
        return packets

    def decode(self, packet: bytes) -> bytes:
        return self.decoder.decode(packet, 1440)

    def reset_input(self) -> None:
        self.pending.clear()


def readiness(config: AppConfig) -> dict[str, object]:
    asr = Path(config.asr.model_path)
    vad = Path(config.vad.model_path) / "src/silero_vad/data/silero_vad.onnx"
    return {
        "asr_model": (asr / "model.pt").is_file(),
        "vad_model": vad.is_file(),
        "ffmpeg": bool(shutil.which(os.environ.get("FFMPEG", "ffmpeg"))),
        "packages": {name: importlib.util.find_spec(name) is not None for name in (
            "funasr", "torch", "onnxruntime", "openai", "edge_tts", "opuslib_next"
        )},
    }


def create_local_test_app(
    config: AppConfig,
    *,
    vad_factory: Callable[[], object] | None = None,
) -> web.Application:
    """Create the browser test app.

    The production default creates one streaming Silero provider per browser
    connection.  A factory is accepted for tests so they can use a small
    deterministic VAD without loading the ONNX model.
    """
    app = web.Application()
    assets = Path(__file__).resolve().parents[2] / "local_test"
    voice_url = f"ws://{_local_host(config.server.host)}:{config.server.ws_port}/xiaozhi/v1/"
    ota_url = f"http://{_local_host(config.server.ota_host)}:{config.server.ota_port}"
    connections: set[web.WebSocketResponse] = set()
    camera_connections: set[web.WebSocketResponse] = set()
    camera_mcp = CameraMCPBridge()

    async def index(request: web.Request) -> web.FileResponse:
        return web.FileResponse(assets / "index.html")

    async def info(request: web.Request) -> web.Response:
        public_ws = config.server.public_websocket_url or voice_url
        hostname = urlsplit(public_ws).hostname or "127.0.0.1"
        authority = f"[{hostname}]" if ":" in hostname else hostname
        return web.json_response({
            "websocket_url": public_ws,
            "bridge_target": voice_url,
            "ota_url": f"http://{authority}:{config.server.ota_port}/xiaozhi/ota/",
            "camera_mcp_url": f"http://{_local_host(config.local_test.host)}:{config.local_test.port}/mcp",
            "lan_ip": hostname,
            "input_sample_rate": 16000, "output_sample_rate": 24000, "frame_duration_ms": 60,
            "max_recording_seconds": config.server.max_recording_seconds,
            "vad_min_silence_duration_ms": config.vad.min_silence_duration_ms,
            "readiness": readiness(config),
            "llm_model": config.llm.model,
            "camera_tools_enabled": config.mcp.enabled,
            "vision_model": config.vision.model if config.vision.enabled else None,
        })

    async def health(request: web.Request) -> web.Response:
        async def probe_voice():
            try:
                _, writer = await wait_for(
                    asyncio.open_connection(_local_host(config.server.host).strip("[]"), config.server.ws_port), 2
                )
                writer.close()
                await writer.wait_closed()
                return {"ok": True, "detail": "语音端口可连接；模型效果需发起对话验证"}
            except (OSError, TimeoutError):
                return {"ok": False, "detail": "语音服务未启动或端口不可达"}

        async def probe_ota():
            try:
                async with ClientSession(timeout=ClientTimeout(total=2)) as client:
                    async with client.get(ota_url + "/health") as response:
                        ok = response.status == 200 and (await response.json()).get("status") == "ok"
                        return {"ok": ok, "detail": "OTA 健康检查通过" if ok else "OTA 健康响应异常"}
            except Exception:
                return {"ok": False, "detail": "OTA 服务未启动或不可达"}

        async def probe_llm():
            headers = {"Authorization": f"Bearer {config.llm.api_key}"} if config.llm.api_key else {}
            try:
                async with ClientSession(timeout=ClientTimeout(total=3), trust_env=True) as client:
                    async with client.get(config.llm.base_url.rstrip("/") + "/models", headers=headers) as response:
                        if response.status != 200:
                            return {"ok": False, "detail": f"模型列表查询失败（HTTP {response.status}）；可运行 LLM 测速验证"}
                        models = [item["id"] for item in (await response.json()).get("data", [])]
                found = any(name == config.llm.model or name == config.llm.model + ":latest" for name in models)
                return {
                    "ok": True if found else None,
                    "detail": "已找到配置的 LLM 模型；生成能力需发起对话验证" if found else "模型服务可达；列表未列出配置模型，请以 LLM 实际测速为准",
                }
            except Exception:
                return {"ok": False, "detail": "LLM 模型列表不可达；可运行 LLM 测速验证"}

        voice, ota, llm = await asyncio.gather(probe_voice(), probe_ota(), probe_llm())
        return web.json_response({"voice": voice, "ota": ota, "llm": llm})

    async def bridge(request: web.Request) -> web.WebSocketResponse:
        # A local test page may use the configured device token, so reject cross-origin use.
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        browser = web.WebSocketResponse(max_msg_size=65536, heartbeat=30)
        await browser.prepare(request)
        connections.add(browser)
        try:
            setup = await browser.receive_json(timeout=10)
            if not isinstance(setup, dict) or setup.get("type") != "connect":
                raise ValueError("First message must be connect")
            device = setup.get("device_id", "browser-test")
            token = setup.get("token") or config.auth.token
            if not isinstance(device, str) or not device.strip() or not isinstance(token, str):
                raise ValueError("Invalid connection settings")
            headers = {"Device-Id": device, "Client-Id": "deskbot-local-test"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=5)) as client:
                async with client.ws_connect(voice_url, headers=headers, max_msg_size=65536) as upstream:
                    bridge_hello = dict(HELLO)
                    bridge_hello["features"] = {"local_test_turn_events": True}
                    await upstream.send_json(bridge_hello)
                    hello = await upstream.receive_json(timeout=config.server.hello_timeout_s)
                    if hello.get("type") != "hello" or hello.get("audio_params", {}).get("sample_rate") != 24000:
                        raise ValueError("Incompatible voice hello")
                    codec = BrowserCodec()
                    await browser.send_json(hello)
                    await browser.send_json({"type": "test.connected"})
                    recording = False
                    continuous = False
                    awaiting_reply = False
                    reply_ready = False
                    heard_speech = False
                    silence_ms = 0.0
                    interruption_speech_ms = 0.0
                    turn_samples = 0
                    silence_limit_ms = float(config.vad.min_silence_duration_ms)
                    max_turn_samples = int(min(30, config.server.max_recording_seconds) * 16000 // 960) * 960
                    pre_roll = bytearray()
                    # Keep the streaming inference state private to this
                    # browser connection.  It is created lazily so a text-only
                    # local test does not need to load the ONNX model.
                    make_vad = vad_factory or (lambda: SileroVADProvider(
                        config.vad.model_path,
                        speech_threshold=config.vad.speech_threshold,
                        silence_threshold=config.vad.silence_threshold,
                    ))
                    vad: object | None = None
                    barrier_number = 0
                    barrier_nonce: str | None = None
                    barrier_reached = asyncio.Event()

                    async def cancel_reply(*, interrupted: bool = False) -> None:
                        """Drain the cancelled turn before accepting new events.

                        The native server processes abort and ping in order.
                        Its matching pong therefore follows the cancelled
                        pipeline's final audio and TTS/session notifications.
                        """
                        nonlocal barrier_number, barrier_nonce
                        barrier_number += 1
                        barrier_nonce = f"continuous-{barrier_number}"
                        barrier_reached.clear()
                        if interrupted:
                            await browser.send_json({"type": "test.continuous", "state": "interrupted"})
                        await upstream.send_json({"type": "abort"})
                        await upstream.send_json({"type": "ping", "nonce": barrier_nonce})
                        await wait_for(barrier_reached.wait(), 5)
                        barrier_nonce = None

                    def reset_vad() -> None:
                        # Test VAD doubles may be stateless.
                        reset = getattr(vad, "reset", None)
                        if reset is not None:
                            reset()

                    async def stop_continuous_turn() -> None:
                        nonlocal recording, awaiting_reply, reply_ready, heard_speech, silence_ms, turn_samples
                        nonlocal interruption_speech_ms
                        if not recording:
                            return
                        for packet in codec.encode(b"", flush=True):
                            await upstream.send_bytes(packet)
                        recording = False
                        awaiting_reply = True
                        reply_ready = False
                        heard_speech = False
                        silence_ms = 0.0
                        interruption_speech_ms = 0.0
                        turn_samples = 0
                        pre_roll.clear()
                        reset_vad()
                        await browser.send_json({"type": "test.continuous", "state": "submitted"})
                        await upstream.send_json({"type": "listen", "state": "stop"})

                    async def to_voice():
                        nonlocal recording, continuous, awaiting_reply, reply_ready, heard_speech, silence_ms, turn_samples, vad
                        nonlocal silence_limit_ms, max_turn_samples, interruption_speech_ms
                        async for message in browser:
                            if message.type == WSMsgType.BINARY:
                                if continuous:
                                    pcm = message.data
                                    if len(pcm) % 2:
                                        raise ValueError("Incomplete PCM sample")
                                    if vad is None:
                                        vad = make_vad()
                                    is_speech = await vad.is_speech(pcm, 16_000)
                                    if not recording:
                                        pre_roll.extend(pcm)
                                        del pre_roll[:-5760]  # about 180 ms at 16 kHz
                                    if awaiting_reply:
                                        duration_ms = len(pcm) / (16_000 * 2) * 1_000
                                        interruption_speech_ms = (
                                            interruption_speech_ms + duration_ms if is_speech else 0.0
                                        )
                                        # Require sustained speech, rather than
                                        # interrupting on a single short noise.
                                        if interruption_speech_ms < 180:
                                            continue
                                        awaiting_reply = False
                                        reply_ready = False
                                        interruption_speech_ms = 0.0
                                        await cancel_reply(interrupted=True)
                                        # The normal recording path below sends
                                        # listen.start with the retained onset.
                                    if not recording:
                                        if not is_speech:
                                            continue
                                        recording = True
                                        heard_speech = True
                                        turn_samples = 0
                                        silence_ms = 0.0
                                        codec.reset_input()
                                        await upstream.send_json({"type": "listen", "state": "start", "mode": "manual"})
                                        await browser.send_json({"type": "test.continuous", "state": "listening"})
                                        pcm = bytes(pre_roll)
                                        pre_roll.clear()
                                    remaining = max_turn_samples - turn_samples
                                    pcm = pcm[:remaining * 2]
                                    if not pcm:
                                        await stop_continuous_turn()
                                        continue
                                    turn_samples += len(pcm) // 2
                                    duration_ms = len(pcm) / (16_000 * 2) * 1_000
                                    if is_speech:
                                        heard_speech = True
                                        silence_ms = 0.0
                                    elif heard_speech:
                                        silence_ms += duration_ms
                                    for packet in codec.encode(pcm):
                                        await upstream.send_bytes(packet)
                                    if (silence_ms >= silence_limit_ms or
                                            turn_samples >= max_turn_samples):
                                        await stop_continuous_turn()
                                    continue
                                if recording:
                                    for packet in codec.encode(message.data):
                                        await upstream.send_bytes(packet)
                            elif message.type == WSMsgType.TEXT:
                                # Segmentation belongs to the browser bridge, while
                                # each utterance uses native listen.start/stop.
                                local = json.loads(message.data)
                                if isinstance(local, dict) and local.get("type") == "continuous.start":
                                    if not hello.get("features", {}).get("local_test_turn_events"):
                                        await browser.send_json({"type": "test.error", "message": "语音服务需要重启后才能使用持续对话"})
                                        await browser.send_json({"type": "test.continuous", "state": "stopped"})
                                        continue
                                    requested = float(local.get("max_turn_seconds", 30))
                                    silence_limit_ms = float(
                                        local.get("silence_ms", config.vad.min_silence_duration_ms)
                                    )
                                    if not math.isfinite(requested) or not math.isfinite(silence_limit_ms):
                                        raise ValueError("Invalid continuous recording limit")
                                    max_turn_samples = int(min(30, config.server.max_recording_seconds, requested) * 16000 // 960) * 960
                                    if max_turn_samples < 960:
                                        raise ValueError("Recording limit must allow one Opus frame")
                                    silence_limit_ms = max(300.0, min(3000.0, silence_limit_ms))
                                    # Starting a mode also cancels an earlier normal reply.
                                    await cancel_reply()
                                    # A new continuous session starts with clean
                                    # Silero recurrent state.  The provider is
                                    # otherwise retained across turns so normal
                                    # multi-turn use does not reload ONNX.
                                    vad = make_vad()
                                    continuous = True
                                    heard_speech = False
                                    silence_ms = 0.0
                                    interruption_speech_ms = 0.0
                                    awaiting_reply = False
                                    reply_ready = False
                                    recording = False
                                    turn_samples = 0
                                    pre_roll.clear()
                                    codec.reset_input()
                                    await browser.send_json({"type": "test.continuous", "state": "started", "silence_ms": silence_limit_ms, "max_turn_seconds": max_turn_samples / 16000})
                                    continue
                                if isinstance(local, dict) and local.get("type") == "continuous.stop":
                                    continuous = False
                                    awaiting_reply = False
                                    reply_ready = False
                                    recording = False
                                    interruption_speech_ms = 0.0
                                    codec.reset_input()
                                    pre_roll.clear()
                                    await upstream.send_json({"type": "abort"})
                                    await browser.send_json({"type": "test.continuous", "state": "stopped"})
                                    continue
                                if isinstance(local, dict) and local.get("type") == "continuous.submit":
                                    if continuous:
                                        await stop_continuous_turn()
                                    continue
                                if isinstance(local, dict) and local.get("type") == "continuous.resume":
                                    if continuous and awaiting_reply and reply_ready:
                                        awaiting_reply = False
                                        reply_ready = False
                                        recording = False
                                        heard_speech = False
                                        silence_ms = 0.0
                                        interruption_speech_ms = 0.0
                                        pre_roll.clear()
                                        codec.reset_input()
                                        reset_vad()
                                        await browser.send_json({"type": "test.continuous", "state": "started"})
                                    continue
                                control = parse_client_message(message.data)
                                if isinstance(control, AbortMessage):
                                    recording = False
                                    continuous = False
                                    awaiting_reply = False
                                    reply_ready = False
                                    heard_speech = False
                                    silence_ms = 0.0
                                    interruption_speech_ms = 0.0
                                    pre_roll.clear()
                                    codec.reset_input()
                                elif isinstance(control, ListenMessage):
                                    continuous = False
                                    awaiting_reply = False
                                    reply_ready = False
                                    interruption_speech_ms = 0.0
                                    pre_roll.clear()
                                    if control.state == "start":
                                        recording = True
                                        codec.reset_input()
                                    elif control.state == "stop":
                                        if recording:
                                            for packet in codec.encode(b"", flush=True):
                                                await upstream.send_bytes(packet)
                                        recording = False
                                        continuous = False
                                    elif control.state == "detect":
                                        recording = False
                                        codec.reset_input()
                                else:
                                    raise ValueError("Unsupported browser message")
                                await upstream.send_str(message.data)
                            elif message.type == WSMsgType.ERROR:
                                break

                    async def from_voice():
                        nonlocal reply_ready
                        async for message in upstream:
                            if barrier_nonce is not None:
                                if message.type == WSMsgType.TEXT:
                                    event = message.json()
                                    if event.get("type") == "pong" and event.get("nonce") == barrier_nonce:
                                        barrier_reached.set()
                                continue
                            if message.type == WSMsgType.BINARY:
                                await browser.send_bytes(codec.decode(message.data))
                            elif message.type == WSMsgType.TEXT:
                                event = message.json()
                                if event.get("type") == "pong":
                                    continue
                                if event.get("type") == "session" and event.get("state") == "idle":
                                    if continuous and awaiting_reply:
                                        reply_ready = True
                                        await browser.send_json({"type": "test.continuous", "state": "reply_done"})
                                    continue
                                await browser.send_str(message.data)
                        if not browser.closed:
                            await browser.send_json({"type": "test.error", "message": f"语音连接已关闭（{upstream.close_code}）"})

                    async def keepalive():
                        while True:
                            await asyncio.sleep(min(30, config.server.idle_timeout_s / 2))
                            if hello.get("features", {}).get("local_test_turn_events"):
                                await upstream.send_json({"type": "ping"})

                    tasks = [asyncio.create_task(to_voice()), asyncio.create_task(from_voice()), asyncio.create_task(keepalive())]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as error:
            if not browser.closed:
                # Avoid echoing addresses with credentials or raw auth headers.
                await browser.send_json({"type": "test.error", "message": f"连接或音频处理失败（{type(error).__name__}），请检查语音服务和配置。"})
        finally:
            connections.discard(browser)
            await browser.close()
        return browser

    async def camera_bridge(request: web.Request) -> web.WebSocketResponse:
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        browser = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024, heartbeat=30)
        await browser.prepare(request)
        camera_connections.add(browser)
        await camera_mcp.register(browser)
        try:
            await browser.send_json({"type": "camera.mcp.ready"})
            async for message in browser:
                if message.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("type") == "camera.mcp.result":
                        await camera_mcp.resolve(payload, browser)
                    elif isinstance(payload, dict) and payload.get("type") == "camera.mcp.bind":
                        session_id = payload.get("session_id")
                        if session_id is None or isinstance(session_id, str):
                            await camera_mcp.bind(browser, session_id)
                            await browser.send_json({"type": "camera.mcp.bound", "session_id": session_id})
        finally:
            camera_connections.discard(browser)
            await camera_mcp.unregister(browser)
            await browser.close()
        return browser

    async def mcp(request: web.Request) -> web.Response:
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        try:
            payload = await request.json()
            result = await camera_mcp.dispatch(payload)
            if isinstance(payload, dict) and payload.get("method") == "notifications/initialized" and "id" not in payload:
                return web.Response(status=202)
            return web.json_response(result)
        except (json.JSONDecodeError, ValueError):
            return web.json_response(CameraMCPBridge._error(None, -32700, "Parse error"), status=400)

    async def mcp_status(request: web.Request) -> web.Response:
        return web.json_response({"name": "deskbot-camera", "tools": True})

    async def shutdown(application):
        await asyncio.gather(
            *(ws.close(code=1001) for ws in tuple(connections) + tuple(camera_connections)),
            return_exceptions=True,
        )

    app.router.add_get("/", index)
    app.router.add_get("/api/info", info)
    app.router.add_get("/api/health", health)
    app.router.add_get("/ws", bridge)
    app.router.add_get("/camera-mcp", camera_bridge)
    app.router.add_post("/mcp", mcp)
    app.router.add_get("/mcp", mcp_status)
    app.router.add_static("/", assets, show_index=False)
    app.on_shutdown.append(shutdown)
    return app
