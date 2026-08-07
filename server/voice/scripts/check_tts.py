from __future__ import annotations

import argparse
import asyncio
import sys
import wave
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the configured Edge TTS provider")
    parser.add_argument("text", help="Text to synthesize")
    parser.add_argument("--config", default="config.yaml", type=Path)
    parser.add_argument("--output", default=Path("data/check-tts.wav"), type=Path)
    return parser


async def _run(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from voice_server.config import load_config
    from voice_server.providers.edge_tts import EdgeTTSProvider

    config = load_config(args.config)
    provider = EdgeTTSProvider(
        voice=config.tts.voice,
        rate=config.tts.rate,
        volume=config.tts.volume,
    )
    pcm = b"".join([chunk async for chunk in provider.synthesize(args.text)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.output), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(24_000)
        destination.writeframes(pcm)
    print(args.output)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as error:
        print(f"TTS check failed ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
