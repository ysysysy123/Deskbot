"""Command line entry point for the Deskbot vision service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import VisionError
from .camera import capture_camera_image, scan_camera_indexes
from .providers import build_analyzer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze one static image.")
    parser.add_argument("image", nargs="?", help="Path to the image file.")
    parser.add_argument(
        "--prompt",
        default="Describe the image for a desktop companion robot.",
        help="Prompt or task hint stored with the result.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--output", help="Optional path for the JSON result.")
    parser.add_argument(
        "--provider",
        choices=["local", "zhipu", "auto"],
        default="local",
        help="Vision provider. 'auto' reads VISION_PROVIDER from .env.",
    )
    parser.add_argument("--env-file", help="Optional .env path. Defaults to .env in the current directory.")
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retry count for transient cloud-provider failures such as HTTP 429.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="Seconds to wait between cloud-provider retries.",
    )
    parser.add_argument(
        "--capture-camera",
        help="Capture one PC camera frame to this image path before analysis.",
    )
    parser.add_argument("--camera-index", type=int, default=0, help="PC camera index for --capture-camera.")
    parser.add_argument(
        "--camera-warmup-frames",
        type=int,
        default=5,
        help="Frames to discard before saving the captured frame.",
    )
    parser.add_argument("--camera-width", type=int, help="Optional requested camera frame width.")
    parser.add_argument("--camera-height", type=int, help="Optional requested camera frame height.")
    parser.add_argument("--list-cameras", action="store_true", help="List PC camera indexes and exit.")
    parser.add_argument("--camera-scan-max", type=int, default=5, help="Highest camera index for --list-cameras.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.list_cameras:
            payload = {
                "schema_version": "deskbot.vision.camera_scan.v0",
                "cameras": scan_camera_indexes(args.camera_scan_max),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        image_path = _image_path_from_args(args, parser)
        analyzer = build_analyzer(args.provider, args.env_file, args.retries, args.retry_delay)
        result = analyzer.analyze(str(image_path), prompt=args.prompt)
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


def _image_path_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Path:
    if args.capture_camera:
        return capture_camera_image(
            args.capture_camera,
            camera_index=args.camera_index,
            warmup_frames=args.camera_warmup_frames,
            width=args.camera_width,
            height=args.camera_height,
        )
    if args.image:
        return Path(args.image)
    parser.error("image is required unless --capture-camera is provided.")


if __name__ == "__main__":
    raise SystemExit(main())
