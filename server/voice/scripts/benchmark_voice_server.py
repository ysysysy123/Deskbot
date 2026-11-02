"""Measure real providers over a temporary loopback voice WebSocket server."""

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

from benchmark_voice_segments import distribution


async def run(args):
    import opuslib_next
    import websockets
    from voice_server.app import ServerApplication
    from voice_server.config import load_config
    from voice_server.performance import read_audio

    config = load_config(args.config)
    pcm = await read_audio(args.audio)
    if len(pcm) / 32000 > config.server.max_recording_seconds:
        raise ValueError("input exceeds recording limit")
    encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
    packets = [encoder.encode(pcm[offset:offset + 1920].ljust(1920, b"\0"), 960)
               for offset in range(0, len(pcm), 1920)]
    runs = []
    with tempfile.TemporaryDirectory(prefix="deskbot-websocket-bench-") as directory:
        # All listeners use private ephemeral ports; production config/data stay untouched.
        isolated = replace(config,
            server=replace(config.server, host="127.0.0.1", ota_host="127.0.0.1", ws_port=0, ota_port=0),
            admin_api=replace(config.admin_api, host="127.0.0.1", port=0),
            auth=replace(config.auth, mode="none", token="", allowed_devices=()),
            memory=replace(config.memory, database_path=str(Path(directory) / "memory.db")))
        startup = time.monotonic()
        application = ServerApplication.from_config(isolated)
        await application.start()
        startup_ms = round((time.monotonic() - startup) * 1000, 1)
        server = application._listeners[0]
        port = server.sockets[0].getsockname()[1]
        cpu_started, wall_started = time.process_time(), time.monotonic()
        try:
            for index in range(args.warmup + args.repeat):
                warmup = index < args.warmup
                async with websockets.connect(f"ws://127.0.0.1:{port}/xiaozhi/v1/",
                    additional_headers={"Device-Id": f"synthetic-latency-{index}"}) as client:
                    await client.send(json.dumps({"type": "hello", "version": 1, "transport": "websocket",
                        "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}}))
                    hello = json.loads(await client.recv())
                    session = next(session for session in application._websocket_server._active_sessions.values()
                                   if session.session_id == hello["session_id"])
                    await client.send('{"type":"listen","state":"start","mode":"manual"}')
                    for packet in packets:
                        await client.send(packet)
                    await client.send('{"type":"listen","state":"stop"}')
                    submitted = time.monotonic()
                    first_audio, received = None, 0
                    timeout = config.asr.timeout_s + config.llm.timeout_s + config.tts.timeout_s + 30
                    while True:
                        raw = await asyncio.wait_for(client.recv(), timeout)
                        if isinstance(raw, bytes):
                            received += 1
                            if first_audio is None:
                                first_audio = (time.monotonic() - submitted) * 1000
                        else:
                            message = json.loads(raw)
                            if message.get("type") == "tts" and message.get("state") == "stop":
                                break
                    client_total = (time.monotonic() - submitted) * 1000
                    await session.wait_until_idle()
                    metrics = {key: value for key, value in (session.last_turn or {}).items() if key != "input_text"}
                    metrics.update(client_submit_to_first_audio_ms=round(first_audio, 1) if first_audio is not None else None,
                                   client_submit_to_stop_ms=round(client_total, 1), received_audio_packets=received)
                    runs.append({"run": index + 1 - args.warmup, "warmup": warmup, "metrics": metrics})
                    print(f"{'warmup' if warmup else str(index + 1 - args.warmup) + '/' + str(args.repeat)}: {metrics['status']}, first audio={metrics.get('first_audio_ms')} ms", file=sys.stderr, flush=True)
            wall_s = time.monotonic() - wall_started
            cpu_s = time.process_time() - cpu_started
            asr = application._websocket_server._asr
            device = getattr(asr._model, "kwargs", {}).get("device")
            torch = sys.modules.get("torch")
            gpu_allocated = torch.cuda.max_memory_allocated() / 1048576 if torch is not None and torch.cuda.is_available() else None
            resources = {"provider_startup_ms": startup_ms, "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                         "serial_test_wall_s": round(wall_s, 1), "process_cpu_s": round(cpu_s, 2),
                         "mean_cpu_core_percent": round(cpu_s / wall_s * 100, 1), "asr_device": str(device),
                         "peak_gpu_allocated_mib": round(gpu_allocated, 1) if gpu_allocated is not None else None}
        finally:
            await application.stop()
    selected = [row["metrics"] for row in runs if not row["warmup"] and row["metrics"]["status"] == "success" and row["metrics"].get("received_audio_packets", 0)]
    keys = ("asr_ms", "memory_ms", "first_text_ms", "first_sentence_ms", "first_tts_request_ms", "first_pcm_ms",
            "first_audio_ms", "total_ms", "output_audio_ms", "tts_requests", "client_submit_to_first_audio_ms", "client_submit_to_stop_ms")
    summary = {key: distribution([row[key] for row in selected if isinstance(row.get(key), (int, float))])
               for key in keys if any(isinstance(row.get(key), (int, float)) for row in selected)}
    return {"ok": len(selected) == args.repeat, "model": config.llm.model, "tts_provider": config.tts.provider,
            "input_audio": args.audio.name, "input_audio_seconds": round(len(pcm) / 32000, 3),
            "repeat": args.repeat, "warmup": args.warmup, "successful_measured_turns": len(selected),
            "scenario": "real ASR/LLM/TTS/Opus through an isolated loopback WebSocket server, one client at a time",
            "notes": ["Each turn uses a fresh synthetic device ID; no prior context; warmups excluded from percentiles.",
                      "Fixed input audio and configured reply constraints; generated answer lengths can differ.",
                      "No live microphone, VAD endpoint wait, ESP32, speaker or acoustic echo in this scenario.",
                      "Listeners use ephemeral ports and temporary SQLite; existing service/config/history are not modified.",
                      "CPU is process core-equivalent percentage across serial turns; GPU memory is allocated peak for this process, not total device usage."],
            "resources": resources, "summary": summary, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description="临时随机端口服务器上的真实语音 WebSocket 串行测速")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat >= 1, warmup >= 0")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    with redirect_stdout(sys.stderr):
        report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "resources": report["resources"]}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
