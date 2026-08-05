"""Zhipu vision adapter using an OpenAI-compatible chat completions request."""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .analyzer import ImageInfo, VisionError, VisionFinding, VisionResult, detect_image_info
from .config import VisionConfig, load_config


class ZhipuVisionAnalyzer:
    """Analyze an image with a Zhipu GLM visual model."""

    provider_name = "zhipu"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: int = 60,
        thinking: Optional[str] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.thinking = thinking

    @classmethod
    def from_config(cls, config: Optional[VisionConfig] = None) -> "ZhipuVisionAnalyzer":
        config = config or load_config()
        if not config.zhipu_api_key:
            raise VisionError("ZHIPUAI_API_KEY is required for the zhipu vision provider.")
        return cls(
            api_key=config.zhipu_api_key,
            base_url=config.zhipu_base_url,
            model=config.zhipu_vision_model,
            thinking=config.zhipu_thinking,
        )

    @property
    def adapter_name(self) -> str:
        return f"zhipu:{self.model}"

    def analyze(self, image_path: str, prompt: str = "Describe the image.") -> VisionResult:
        path = Path(image_path)
        data, image_info = _read_image(path)
        response = self._create_chat_completion(data, image_info, prompt)
        summary = _extract_message_text(response)
        if not summary:
            raise VisionError("Zhipu response did not include message content.")

        return VisionResult(
            schema_version="deskbot.vision.result.v0",
            adapter=self.adapter_name,
            source_path=str(path),
            prompt=prompt,
            image=image_info,
            summary=summary,
            tags=[image_info.format.lower(), image_info.orientation, "cloud", self.provider_name],
            findings=[
                VisionFinding(
                    label="input.valid_image",
                    confidence=1.0,
                    detail="The image header was recognized and basic dimensions were decoded.",
                ),
                VisionFinding(
                    label="model.description",
                    confidence=0.86,
                    detail=summary,
                ),
            ],
            errors=[],
        )

    def _create_chat_completion(
        self, image_data: bytes, image_info: ImageInfo, prompt: str
    ) -> Dict[str, Any]:
        request_body: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": _data_uri(image_data, image_info.mime_type),
                            },
                        },
                    ],
                }
            ],
        }

        thinking = _thinking_payload(self.thinking)
        if thinking is not None:
            request_body["thinking"] = thinking

        endpoint = _chat_completions_endpoint(self.base_url)
        payload = json.dumps(request_body).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
                context=_ssl_context(),
            ) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VisionError(f"Zhipu API returned HTTP {exc.code}: {_redact(detail)}") from exc
        except urllib.error.URLError as exc:
            raise VisionError(f"Zhipu API request failed: {exc.reason}") from exc
        except ssl.SSLError as exc:
            raise VisionError(f"Zhipu API TLS setup failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise VisionError("Zhipu API response was not valid JSON.") from exc


def _read_image(path: Path) -> tuple[bytes, ImageInfo]:
    if not path.exists():
        raise VisionError(f"Image does not exist: {path}")
    if not path.is_file():
        raise VisionError(f"Image path is not a file: {path}")

    data = path.read_bytes()
    if not data:
        raise VisionError(f"Image file is empty: {path}")
    return data, detect_image_info(data)


def _data_uri(data: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _chat_completions_endpoint(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def _ssl_context() -> ssl.SSLContext:
    cafile = _default_cafile()
    if cafile:
        return ssl.create_default_context(cafile=str(cafile))
    return ssl.create_default_context()


def _default_cafile() -> Optional[Path]:
    env_cafile = os.environ.get("SSL_CERT_FILE")
    if env_cafile and Path(env_cafile).exists():
        return Path(env_cafile)

    conda_cafile = Path(sys.prefix) / "Library" / "ssl" / "cacert.pem"
    if conda_cafile.exists():
        return conda_cafile

    pip_vendor_cafile = Path(sys.prefix) / "Lib" / "site-packages" / "pip" / "_vendor" / "certifi" / "cacert.pem"
    if pip_vendor_cafile.exists():
        return pip_vendor_cafile

    return None


def _extract_message_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""


def _thinking_payload(value: Optional[str]) -> Optional[Dict[str, str]]:
    if not value:
        return None

    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"disabled", "disable", "off", "false", "0", "no"}:
        return {"type": "disabled"}
    if normalized in {"enabled", "enable", "on", "true", "1", "yes"}:
        return {"type": "enabled"}

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {"type": normalized}

    if isinstance(decoded, dict):
        return {str(key): str(item) for key, item in decoded.items()}
    return {"type": normalized}


def _redact(value: str) -> str:
    return value.replace("Bearer ", "Bearer <redacted>")[:1000]
