"""Separate Edge TTS media arrival from FFmpeg startup and PCM output."""

import argparse
import asyncio
from contextlib import aclosing
import json
from pathlib import Path
import sys
import time
import wave

from benchmark_voice_segments import distribution


async def measure(config, text, *, blocksize=None):
    from edge_tts import Communicate
    from voice_server.audio.transcoder import FFmpegTranscoder
    from voice_server.providers.edge_tts import EdgeTTSProvider

    metrics = {"media_chunks": 0, "media_bytes": 0, "pcm_bytes": 0}
    pcm = bytearray()
    started = time.perf_counter()

    def elapsed():
        return (time.perf_counter() - started) * 1000

    class Communicator:
        def __init__(self, **kwargs):
            self.source = Communicate(**kwargs)

        async def stream(self):
            async with aclosing(self.source.stream()) as events:
                async for event in events:
                    if event.get("type") == "audio" and event["data"]:
                        metrics.setdefault("first_media_ms", elapsed())
                        metrics["last_media_ms"] = elapsed()
                        metrics["media_chunks"] += 1
                        metrics["media_bytes"] += len(event["data"])
                    yield event

    async def spawn(*command, **kwargs):
        command = list(command)
        if blocksize is not None:
            position = command.index("-i")
            command[position:position] = ["-blocksize", str(blocksize)]
        process = await asyncio.create_subprocess_exec(*command, **kwargs)
        metrics["transcoder_spawn_ms"] = elapsed()
        return process

    provider = EdgeTTSProvider(
        voice=config.tts.voice, rate=config.tts.rate, volume=config.tts.volume,
        communicate_factory=Communicator,
        transcoder=FFmpegTranscoder(subprocess_factory=spawn),
    )
    try:
        async with asyncio.timeout(config.tts.timeout_s):
            async with aclosing(provider.synthesize(text)) as audio:
                async for part in audio:
                    if part:
                        metrics.setdefault("first_pcm_ms", elapsed())
                        metrics["pcm_bytes"] += len(part)
                        pcm.extend(part)
        metrics["status"] = "success"
    except Exception as error:
        metrics.update(status="error", error_type=type(error).__name__)
    metrics["total_ms"] = elapsed()
    if "first_pcm_ms" in metrics:
        metrics["media_to_pcm_ms"] = metrics["first_pcm_ms"] - metrics["first_media_ms"]
    metrics["output_audio_ms"] = metrics["pcm_bytes"] / 48
    return {key: round(value, 2) if isinstance(value, float) else value for key, value in metrics.items()}, bytes(pcm)


def summarize(runs):
    result = {}
    for mode in ("default", "small_reads"):
        measured = [row["metrics"] for row in runs if row["mode"] == mode and not row["warmup"]]
        success = [row for row in measured if row["status"] == "success"]
        keys = ("first_media_ms", "transcoder_spawn_ms", "first_pcm_ms", "media_to_pcm_ms",
                "total_ms", "output_audio_ms", "media_chunks", "media_bytes", "pcm_bytes")
        result[mode] = {
            "measured": len(measured), "successful": len(success), "failed": len(measured) - len(success),
            "metrics": {key: distribution([row[key] for row in success if key in row])
                        for key in keys if any(key in row for row in success)},
        }
    return result


async def run(args):
    from voice_server.config import load_config

    config = load_config(args.config)
    if config.tts.provider != "edge":
        raise ValueError("this stage benchmark requires the Edge TTS provider")
    runs, saved = [], set()
    for index in range(args.warmup + args.repeat):
        warmup = index < args.warmup
        modes = ("default", "small_reads") if index % 2 == 0 else ("small_reads", "default")
        for mode in modes:
            metrics, pcm = await measure(config, args.text, blocksize=1024 if mode == "small_reads" else None)
            runs.append({"mode": mode, "run": index + 1 - args.warmup, "warmup": warmup, "metrics": metrics})
            print(f"{mode} {'warmup' if warmup else str(index + 1 - args.warmup)}: {metrics}", file=sys.stderr, flush=True)
            if args.audio_dir and mode not in saved and metrics["status"] == "success":
                args.audio_dir.mkdir(parents=True, exist_ok=True)
                with wave.open(str(args.audio_dir / f"{mode}.wav"), "wb") as output:
                    output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    output.writeframes(pcm)
                saved.add(mode)
    summary = summarize(runs)
    return {
        "ok": all(row["metrics"]["status"] == "success" for row in runs),
        "scenario": "fixed text -> real Edge TTS -> instrumented FFmpeg -> unpaced PCM consumer",
        "text": args.text, "voice": config.tts.voice, "rate": config.tts.rate,
        "repeat_per_mode": args.repeat, "warmup_per_mode": args.warmup,
        "notes": [
            "Alternating mode order; default FFmpeg pipe reads versus input -blocksize 1024.",
            "Warmups excluded from percentiles; failed measured runs counted separately.",
            "All stage timestamps are relative to one TTS request on the same monotonic clock.",
            "transcoder_spawn_ms means process creation returned, not decoder or audio-device readiness.",
            "No ASR, LLM, memory, production service, ESP32 or speaker in this benchmark.",
        ],
        "summary": summary, "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser(description="固定文本的 Edge TTS 首媒体、FFmpeg 启动与首 PCM 对比")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--text", default="我会先把整个问题拆成几个清楚的步骤，再逐项说明具体的处理方法。")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--audio-dir", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("repeat >= 1, warmup >= 0")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
