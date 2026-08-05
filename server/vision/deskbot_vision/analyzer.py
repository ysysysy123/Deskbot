"""Static-image analysis contract for the Deskbot vision service.

The first implementation deliberately uses only the Python standard library so
the service can run on a fresh Windows PC before API keys or model weights are
available. Model-backed adapters can reuse the output contract later.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


class VisionError(Exception):
    """Raised when an image cannot be accepted by the vision service."""


@dataclass(frozen=True)
class ImageInfo:
    format: str
    mime_type: str
    width: int
    height: int
    byte_size: int

    @property
    def orientation(self) -> str:
        if self.width == self.height:
            return "square"
        if self.width > self.height:
            return "landscape"
        return "portrait"


@dataclass(frozen=True)
class VisionFinding:
    label: str
    confidence: float
    detail: str


@dataclass(frozen=True)
class VisionResult:
    schema_version: str
    adapter: str
    source_path: str
    prompt: str
    image: ImageInfo
    summary: str
    tags: List[str]
    findings: List[VisionFinding]
    errors: List[str]

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["image"]["orientation"] = self.image.orientation
        return payload

    def to_json(self, *, pretty: bool = False) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=2 if pretty else None,
            sort_keys=pretty,
        )


class StaticImageAnalyzer:
    """Accepts one static image and returns a structured local analysis."""

    adapter_name = "local-static-image-v0"

    def analyze(self, image_path: str, prompt: str = "Describe the image.") -> VisionResult:
        path = Path(image_path)
        if not path.exists():
            raise VisionError(f"Image does not exist: {path}")
        if not path.is_file():
            raise VisionError(f"Image path is not a file: {path}")

        data = path.read_bytes()
        if not data:
            raise VisionError(f"Image file is empty: {path}")

        image_info = _detect_image_info(data)
        color_label = _dominant_color_for_ppm_p3(data)
        tags = [image_info.format.lower(), image_info.orientation]
        findings = [
            VisionFinding(
                label="input.valid_image",
                confidence=1.0,
                detail="The image header was recognized and basic dimensions were decoded.",
            ),
            VisionFinding(
                label="image.dimensions",
                confidence=1.0,
                detail=f"{image_info.width}x{image_info.height} pixels.",
            ),
        ]

        if color_label:
            tags.append(color_label)
            findings.append(
                VisionFinding(
                    label=f"color.{color_label}",
                    confidence=0.72,
                    detail="Dominant color estimated from a plain-text PPM sample.",
                )
            )

        summary_parts = [
            f"Accepted {image_info.format} image",
            f"{image_info.width}x{image_info.height}",
            image_info.orientation,
        ]
        if color_label:
            summary_parts.append(f"dominant color: {color_label}")

        return VisionResult(
            schema_version="deskbot.vision.result.v0",
            adapter=self.adapter_name,
            source_path=str(path),
            prompt=prompt,
            image=image_info,
            summary=", ".join(summary_parts),
            tags=tags,
            findings=findings,
            errors=[],
        )


def _detect_image_info(data: bytes) -> ImageInfo:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24:
            raise VisionError("PNG header is incomplete.")
        width, height = struct.unpack(">II", data[16:24])
        return ImageInfo("PNG", "image/png", width, height, len(data))

    if data.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(data)
        return ImageInfo("JPEG", "image/jpeg", width, height, len(data))

    if data.startswith((b"GIF87a", b"GIF89a")):
        width, height = struct.unpack("<HH", data[6:10])
        return ImageInfo("GIF", "image/gif", width, height, len(data))

    if data.startswith(b"BM") and len(data) >= 26:
        width = int.from_bytes(data[18:22], "little", signed=True)
        height = abs(int.from_bytes(data[22:26], "little", signed=True))
        return ImageInfo("BMP", "image/bmp", width, height, len(data))

    if data.startswith(b"P3"):
        width, height, _max_value, _values = _parse_ppm_p3(data)
        return ImageInfo("PPM", "image/x-portable-pixmap", width, height, len(data))

    raise VisionError("Unsupported image format. Supported: PNG, JPEG, GIF, BMP, PPM P3.")


def _jpeg_dimensions(data: bytes) -> Tuple[int, int]:
    idx = 2
    while idx < len(data):
        if data[idx] != 0xFF:
            idx += 1
            continue

        while idx < len(data) and data[idx] == 0xFF:
            idx += 1
        if idx >= len(data):
            break

        marker = data[idx]
        idx += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if idx + 2 > len(data):
            break

        segment_length = int.from_bytes(data[idx : idx + 2], "big")
        if segment_length < 2:
            break
        segment_start = idx + 2
        segment_end = idx + segment_length
        if segment_end > len(data):
            break

        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_start + 5 > len(data):
                break
            height = int.from_bytes(data[segment_start + 1 : segment_start + 3], "big")
            width = int.from_bytes(data[segment_start + 3 : segment_start + 5], "big")
            return width, height

        idx = segment_end

    raise VisionError("JPEG dimensions could not be decoded.")


def _parse_ppm_p3(data: bytes) -> Tuple[int, int, int, List[int]]:
    text = data.decode("ascii", errors="strict")
    tokens = list(_ppm_tokens(text.splitlines()))
    if len(tokens) < 4 or tokens[0] != "P3":
        raise VisionError("PPM P3 header is incomplete.")

    width = int(tokens[1])
    height = int(tokens[2])
    max_value = int(tokens[3])
    values = [int(value) for value in tokens[4:]]
    if width <= 0 or height <= 0:
        raise VisionError("PPM dimensions must be positive.")
    if max_value <= 0:
        raise VisionError("PPM max value must be positive.")
    return width, height, max_value, values


def _ppm_tokens(lines: Iterable[str]) -> Iterable[str]:
    for line in lines:
        content = line.split("#", 1)[0].strip()
        if not content:
            continue
        for token in content.split():
            yield token


def _dominant_color_for_ppm_p3(data: bytes) -> Optional[str]:
    if not data.startswith(b"P3"):
        return None

    width, height, max_value, values = _parse_ppm_p3(data)
    expected = width * height * 3
    if len(values) < expected:
        return None

    triples = list(zip(values[0:expected:3], values[1:expected:3], values[2:expected:3]))
    if not triples:
        return None

    scale = 255 / max_value
    red = sum(pixel[0] * scale for pixel in triples) / len(triples)
    green = sum(pixel[1] * scale for pixel in triples) / len(triples)
    blue = sum(pixel[2] * scale for pixel in triples) / len(triples)
    return _label_rgb(red, green, blue)


def _label_rgb(red: float, green: float, blue: float) -> str:
    if max(red, green, blue) < 45:
        return "dark"
    if min(red, green, blue) > 210:
        return "light"
    if red >= green * 1.2 and red >= blue * 1.2:
        return "red"
    if green >= red * 1.2 and green >= blue * 1.2:
        return "green"
    if blue >= red * 1.2 and blue >= green * 1.2:
        return "blue"
    if red > 180 and green > 140 and blue < 110:
        return "warm"
    return "mixed"
