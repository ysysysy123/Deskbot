"""Command line entry point for the Deskbot static vision service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyzer import StaticImageAnalyzer, VisionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze one static image.")
    parser.add_argument("image", help="Path to the image file.")
    parser.add_argument(
        "--prompt",
        default="Describe the image for a desktop companion robot.",
        help="Prompt or task hint stored with the result.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--output", help="Optional path for the JSON result.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = StaticImageAnalyzer().analyze(args.image, prompt=args.prompt)
    except VisionError as exc:
        print(f"vision error: {exc}", file=sys.stderr)
        return 2

    payload = result.to_json(pretty=args.pretty)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
