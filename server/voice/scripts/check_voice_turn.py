"""Run one real ASR/LLM/TTS/Opus turn with an isolated temporary memory DB."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time


async def run(args):
    import opuslib_next
    from voice_server.audio.opus import OpusCodec
    from voice_server.config import load_config
    from voice_server.memory.service import MemoryService
    from voice_server.memory.sqlite import SQLiteMemoryProvider
    from voice_server.performance import create_provider, read_audio
    from voice_server.protocol.messages import ListenMessage
    from voice_server.session import VoiceSession

    config = load_config(args.config)
    pcm = await read_audio(args.audio)
    if len(pcm) / 32000 > config.server.max_recording_seconds:
        raise ValueError("input exceeds configured recording limit")
    started = time.monotonic()
    asr = create_provider("asr", config)
    load_ms = round((time.monotonic() - started) * 1000, 1)
    llm, tts = create_provider("llm", config), create_provider("tts", config)

    class Transport:
        async def send_json(self, message):
            pass

        async def send_bytes(self, packet):
            pass

    try:
        with tempfile.TemporaryDirectory(prefix="deskbot-voice-check-") as directory:
            store = SQLiteMemoryProvider(Path(directory) / "memory.db")
            await store.initialize()
            memory = MemoryService(store, store, llm, config.memory.summary_threshold,
                                   idle_delay_s=config.memory.idle_delay_s)
            # Manual submission uses no VAD; the input WAV already defines the utterance.
            session = VoiceSession(device_id="synthetic-benchmark", session_id="isolated-check",
                transport=Transport(), codec=OpusCodec(), asr=asr, llm=llm, tts=tts, memory=memory, vad=None,
                min_silence_duration_ms=config.vad.min_silence_duration_ms, recent_limit=config.memory.recent_limit,
                asr_timeout_s=config.asr.timeout_s, llm_timeout_s=config.llm.timeout_s, tts_timeout_s=config.tts.timeout_s,
                max_binary_bytes=config.server.max_binary_bytes, max_recording_bytes=config.server.max_recording_bytes,
                max_recording_seconds=config.server.max_recording_seconds, error_text=config.tts.error_text,
                system_prompt=config.dialogue.system_prompt, sentence_max_chars=config.dialogue.sentence_max_chars,
                first_clause_chars=config.dialogue.first_clause_chars if args.first_clause_chars is None else args.first_clause_chars,
                audio_packet_interval_s=.06 if args.paced else 0)
            try:
                await session.handle_message(ListenMessage("start", "manual", None))
                encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
                for offset in range(0, len(pcm), 1920):
                    frame = pcm[offset:offset + 1920].ljust(1920, b"\0")
                    await session.handle_audio(encoder.encode(frame, 960))
                await session.handle_message(ListenMessage("stop", "manual", None))
                await session.wait_until_idle()
                metrics = {key: value for key, value in (session.last_turn or {}).items() if key != "input_text"}
                return {"ok": metrics.get("status") == "success" and metrics.get("audio_packets", 0) > 0,
                        "model": config.llm.model, "temporary_database": True, "input_audio_seconds": round(len(pcm) / 32000, 3),
                        "asr_provider_load_ms": load_ms, "paced_output": args.paced,
                        "first_clause_chars": session._first_clause_chars, "metrics": metrics,
                        "notes": ["单次真实 Provider 检查，不代表 P50/P95。", "未经过实际 WebSocket、VAD、网络设备或扬声器；first_audio_ms 是本进程音频包交给 Transport 的时间。"]}
            finally:
                await session.close()
                await memory.close()
    finally:
        await llm.close()


def main():
    parser = argparse.ArgumentParser(description="临时记忆库中的真实 ASR→LLM→TTS→Opus 检查，不启动或重启现有服务")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--paced", action="store_true", help="按实机 60 ms 节奏输出；默认模拟浏览器快速消费")
    parser.add_argument("--first-clause-chars", type=int, help="覆盖首段阈值，0 可用于整句基线对比")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    try:
        report = asyncio.run(run(args))
    except Exception as error:
        report = {"ok": False, "error": type(error).__name__}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
