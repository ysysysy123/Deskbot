"""Compare sentence/first-clause scheduling with a fixed stream and real TTS."""

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
import wave


PREFIX = "我会先把整个问题拆成几个清楚的步骤，"
TAIL = "再逐项说明具体的处理方法。"


def distribution(values):
    values = sorted(values)
    position = (len(values) - 1) * .95
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return {"n": len(values), "p50": round(statistics.median(values), 1),
            "p95": round(values[lower] + (values[upper] - values[lower]) * (position - lower), 1)}


async def run(args):
    from voice_server.audio.opus import OpusCodec
    from voice_server.config import load_config
    from voice_server.memory.service import MemoryService
    from voice_server.memory.sqlite import SQLiteMemoryProvider
    from voice_server.performance import create_provider
    from voice_server.session import VoiceSession

    config = load_config(args.config)
    tts = create_provider("tts", config)
    audio_samples = {}

    class FixedStream:
        async def stream(self, request):
            yield PREFIX
            await asyncio.sleep(args.stream_gap_ms / 1000)
            yield TAIL

    class UnusedLLM:
        def stream(self, *args, **kwargs):
            raise AssertionError("This benchmark must not invoke a language model")

    class RecordingTTS:
        async def synthesize(self, text):
            capture = text not in audio_samples
            parts = []
            async for pcm in tts.synthesize(text):
                if capture:
                    parts.append(pcm)
                yield pcm
            if capture:
                audio_samples[text] = b"".join(parts)

    class Transport:
        def __init__(self):
            self.packets = []

        async def send_json(self, message):
            pass

        async def send_bytes(self, packet):
            self.packets.append(time.monotonic())

    runs = []
    with tempfile.TemporaryDirectory(prefix="deskbot-segment-bench-") as directory:
        async def one(mode, number, warmup):
            store = SQLiteMemoryProvider(Path(directory) / f"{mode}-{number}-{warmup}.db")
            await store.initialize()
            memory = MemoryService(store, store, UnusedLLM(), 12)
            transport = Transport()
            session = VoiceSession(device_id="synthetic-segments", session_id=f"{mode}-{number}",
                transport=transport, codec=OpusCodec(), asr=None, llm=UnusedLLM(), tts=RecordingTTS(),
                memory=memory, vad=None, min_silence_duration_ms=config.vad.min_silence_duration_ms,
                recent_limit=config.memory.recent_limit, asr_timeout_s=config.asr.timeout_s,
                llm_timeout_s=config.llm.timeout_s, tts_timeout_s=config.tts.timeout_s,
                max_binary_bytes=config.server.max_binary_bytes, max_recording_bytes=config.server.max_recording_bytes,
                max_recording_seconds=config.server.max_recording_seconds, error_text=config.tts.error_text,
                sentence_max_chars=config.dialogue.sentence_max_chars,
                first_clause_chars=18 if mode == "first_clause" else 0,
                audio_packet_interval_s=.06, dialogue_backend=FixedStream())
            try:
                await session.handle_text("请解释处理方法。")
                await session.wait_until_idle()
                metrics = {key: value for key, value in (session.last_turn or {}).items() if key != "input_text"}
                gaps = [(right - left) * 1000 for left, right in zip(transport.packets, transport.packets[1:])]
                metrics["max_packet_gap_ms"] = round(max(gaps, default=0), 1)
                metrics["output_stall_ms"] = round(sum(max(0, gap - 60) for gap in gaps), 1)
                return {"mode": mode, "run": number, "warmup": warmup, "metrics": metrics}
            finally:
                await session.close()
                await memory.close()

        for number in range(args.warmup):
            for mode in ("sentence", "first_clause"):
                result = await one(mode, number + 1, True)
                runs.append(result)
                print(f"warmup {mode}: {result['metrics']['status']}", file=sys.stderr, flush=True)
        for number in range(args.repeat):
            modes = ("sentence", "first_clause") if number % 2 == 0 else ("first_clause", "sentence")
            for mode in modes:
                result = await one(mode, number + 1, False)
                runs.append(result)
                print(f"{number + 1}/{args.repeat} {mode}: {result['metrics']['status']}, first audio={result['metrics'].get('first_audio_ms')} ms", file=sys.stderr, flush=True)

    if args.audio_dir:
        args.audio_dir.mkdir(parents=True, exist_ok=True)
        for name, text in (("prefix", PREFIX), ("tail", TAIL), ("sentence", PREFIX + TAIL)):
            if text in audio_samples:
                with wave.open(str(args.audio_dir / f"{name}.wav"), "wb") as output:
                    output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    output.writeframes(audio_samples[text])
    summaries = {}
    for mode in ("sentence", "first_clause"):
        selected = [run["metrics"] for run in runs if run["mode"] == mode and not run["warmup"] and run["metrics"]["status"] == "success"]
        summaries[mode] = {key: distribution([row[key] for row in selected if key in row])
                           for key in ("first_sentence_ms", "first_pcm_ms", "first_audio_ms", "total_ms",
                                       "output_audio_ms", "tts_requests", "max_packet_gap_ms", "output_stall_ms")
                           if any(key in row for row in selected)}
    return {"ok": all(run["metrics"]["status"] == "success" for run in runs),
            "scenario": "fixed text stream -> real configured TTS -> Opus -> paced in-process transport",
            "text": PREFIX + TAIL, "stream_gap_ms": args.stream_gap_ms, "repeat_per_mode": args.repeat,
            "warmup_per_mode": args.warmup, "tts_provider": config.tts.provider,
            "notes": ["No ASR, real LLM, WebSocket or speaker in this controlled experiment.",
                      "Mode order alternates by pair; warmups excluded; successful runs only in percentiles.",
                      "Packet gaps include scheduling overhead; no playback acknowledgement.",
                      "These are scenario percentiles, not deployed voice-conversation latency."],
            "summary": summaries, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description="固定文本流、真实 TTS 的首段分块受控对比；不启停现有服务")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--stream-gap-ms", type=int, default=600)
    parser.add_argument("--audio-dir", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0 or args.stream_gap_ms < 0:
        parser.error("repeat >= 1, warmup >= 0, stream-gap-ms >= 0")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
