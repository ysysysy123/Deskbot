from __future__ import annotations

import argparse
import asyncio
import sys
import wave
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the configured SenseVoice ASR provider")
    parser.add_argument("audio", type=Path, help="16 kHz mono PCM WAV or another FFmpeg-readable file")
    parser.add_argument("--config", default="config.yaml", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from voice_server.audio.transcoder import FFmpegTranscoder
    from voice_server.config import load_config
    from voice_server.providers.sensevoice import SenseVoiceASRProvider

    config = load_config(args.config)
    try:
        with wave.open(str(args.audio), "rb") as source:
            if (
                source.getframerate() != 16_000
                or source.getsampwidth() != 2
                or source.getnchannels() != 1
                or source.getcomptype() != "NONE"
            ):
                raise ValueError("WAV is not 16 kHz, 16-bit, mono PCM")
            pcm = source.readframes(source.getnframes())
    except (wave.Error, EOFError, ValueError):
        pcm = await FFmpegTranscoder(sample_rate=16_000).to_pcm(args.audio.read_bytes())

    provider = SenseVoiceASRProvider.from_model_path(
        config.asr.model_path,
        max_concurrency=config.asr.max_concurrency,
    )
    print(await provider.transcribe(pcm, 16_000))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as error:
        print(f"ASR check failed ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
