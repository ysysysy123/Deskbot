"""Replay fixed speech and inserted pauses through the configured Silero VAD."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import wave


def endpoints(speech_frames, threshold):
    segments, onset, silence = [], None, 0
    for index, speech in enumerate(speech_frames):
        if speech:
            if onset is None:
                onset = index * 60
            silence = 0
        elif onset is not None:
            silence += 60
            if silence >= threshold:
                segments.append({"onset_ms": onset, "submitted_ms": (index + 1) * 60,
                                 "silence_after_last_vad_speech_ms": silence})
                onset, silence = None, 0
    return segments


async def run(args):
    from voice_server.config import load_config
    from voice_server.performance import read_audio
    from voice_server.providers.silero import SileroVADProvider

    config = load_config(args.config)
    pcm = await read_audio(args.audio)
    # atempo changes speed without changing pitch; all processing remains local.
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", "16000", "-ac", "1",
        "-i", "pipe:0", "-af", "atempo=0.7", "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    slow, errors = await process.communicate(pcm)
    if process.returncode:
        raise RuntimeError("local tempo conversion failed")
    vad = SileroVADProvider(config.vad.model_path, speech_threshold=config.vad.speech_threshold,
                            silence_threshold=config.vad.silence_threshold)
    samples = [("normal", pcm, 0), ("slow_0.7x", slow, 0)]
    midpoint = (len(pcm) // 4) * 2
    for pause in (300, 450, 540, 900):
        samples.append((f"normal_pause_{pause}ms", pcm[:midpoint] + b"\0" * (pause * 32) + pcm[midpoint:], pause))
    samples.append(("silence_only", b"\0" * 64000, 0))
    results = []
    for name, sample, inserted_pause in samples:
        vad.reset()
        frames = []
        padded = sample + b"\0" * 38400  # trailing silence to observe the endpoint
        for offset in range(0, len(padded), 1920):
            frame = padded[offset:offset + 1920].ljust(1920, b"\0")
            frames.append(await vad.is_speech(frame, 16000))
        results.append({"sample": name, "audio_seconds": round(len(sample) / 32000, 3),
                        "inserted_pause_ms": inserted_pause,
                        "450_ms": endpoints(frames, 450), "600_ms": endpoints(frames, 600)})
        if args.audio_dir and name != "silence_only":
            args.audio_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(args.audio_dir / f"{name}.wav"), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(sample)
    return {"source_audio": args.audio.name, "vad": "configured Silero ONNX", "results": results,
            "notes": ["Source is a fixed synthetic utterance; slow speech is locally slowed to 0.7x; pauses are inserted at its midpoint.",
                      "The same Silero decisions feed both endpoint thresholds; 60 ms frame grid gives 480 ms for a 450 ms threshold.",
                      "This tests VAD and endpoint behavior on synthetic speech, not real microphone/acoustic echo or human pause distribution.",
                      "Inserted pause length is not identical to VAD-classified silence length."]}


def main():
    parser = argparse.ArgumentParser(description="固定语音、慢速和人工停顿样本的真实 Silero 450/600 ms 对比")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yaml")
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    report = asyncio.run(run(args))
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{ "sample": row["sample"], "450_ms_turns": len(row["450_ms"]),
                      "600_ms_turns": len(row["600_ms"])} for row in report["results"]], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
