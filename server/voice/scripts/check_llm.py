from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the configured OpenAI-compatible LLM")
    parser.add_argument("text", help="Prompt text")
    parser.add_argument("--config", default="config.yaml", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from voice_server.config import load_config
    from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider

    config = load_config(args.config)
    provider = OpenAICompatibleLLMProvider(
        base_url=config.llm.base_url,
        model=config.llm.model,
        api_key=config.llm.api_key,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout_s=config.llm.timeout_s,
    )
    try:
        async for chunk in provider.stream([{"role": "user", "content": args.text}]):
            print(chunk, end="", flush=True)
        print()
    finally:
        await provider.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as error:
        print(f"LLM check failed ({type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
