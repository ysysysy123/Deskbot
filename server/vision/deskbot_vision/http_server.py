"""HTTP entry point for the Deskbot vision service.

The service mirrors the xiaozhi-server vision route shape by exposing
``/mcp/vision/explain`` on the configured HTTP port, defaulting to 8003.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .analyzer import VisionError, detect_image_info
from .providers import build_analyzer

DEFAULT_PROMPT = "Describe the image for a desktop companion robot."
HTTP_SCHEMA_VERSION = "deskbot.vision.http.v0"


class VisionRequestError(Exception):
    """Raised when an HTTP vision request cannot be parsed."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class VisionHttpConfig:
    provider: str = "auto"
    env_file: Optional[str] = None
    retries: int = 0
    retry_delay: float = 5.0
    upload_dir: str = "build/vision-http"
    default_prompt: str = DEFAULT_PROMPT


class VisionHttpServer(ThreadingHTTPServer):
    def __init__(self, server_address, request_handler_class, vision_config: VisionHttpConfig) -> None:
        super().__init__(server_address, request_handler_class)
        self.vision_config = vision_config


class VisionRequestHandler(BaseHTTPRequestHandler):
    server: VisionHttpServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/health", "/mcp/vision/health"}:
            self._write_json(
                200,
                {
                    "schema_version": HTTP_SCHEMA_VERSION,
                    "status": "ok",
                    "service": "deskbot-vision",
                    "endpoint": "/mcp/vision/explain",
                },
            )
            return

        if path == "/mcp/vision/explain":
            host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_port}"
            self._write_json(
                200,
                {
                    "schema_version": HTTP_SCHEMA_VERSION,
                    "status": "ok",
                    "message": "MCP Vision interface is ready.",
                    "endpoint": f"http://{host}/mcp/vision/explain",
                    "method": "POST",
                },
            )
            return

        self._write_json(404, _error_envelope(None, "not_found", f"Unknown route: {path}"))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/mcp/vision/explain":
            self._write_json(404, _error_envelope(None, "not_found", f"Unknown route: {path}"))
            return

        payload: Dict[str, Any] = {}
        request_id: Optional[str] = None
        try:
            payload = self._read_request_payload()
            request_id = _request_id(payload)
            image_path = _image_from_payload(payload, request_id, self.server.vision_config.upload_dir)
            prompt = str(payload.get("prompt") or self.server.vision_config.default_prompt)
            provider = str(payload.get("provider") or self.server.vision_config.provider)
            analyzer = build_analyzer(
                provider,
                self.server.vision_config.env_file,
                self.server.vision_config.retries,
                self.server.vision_config.retry_delay,
            )
            result = analyzer.analyze(str(image_path), prompt=prompt)
            self._write_json(200, _success_envelope(request_id, payload, result.to_dict()))
        except VisionRequestError as exc:
            request_id = request_id or _safe_request_id(payload)
            self._write_json(exc.status, _error_envelope(request_id, exc.code, str(exc), payload))
        except VisionError as exc:
            request_id = request_id or _safe_request_id(payload)
            self._write_json(422, _error_envelope(request_id, "vision_error", str(exc), payload))
        except Exception as exc:  # pragma: no cover - defensive server boundary
            request_id = request_id or _safe_request_id(payload)
            self._write_json(500, _error_envelope(request_id, "internal_error", str(exc), payload))

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)

    def _read_request_payload(self) -> Dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return self._read_json_body()
        if "multipart/form-data" in content_type:
            return self._read_multipart_body(content_type)
        raise VisionRequestError(
            "unsupported_content_type",
            "Only application/json and multipart/form-data requests are supported.",
        )

    def _read_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self._read_chunked_body()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise VisionRequestError("invalid_content_length", "Content-Length must be an integer.") from exc

        if length <= 0:
            raise VisionRequestError("empty_body", "Request body is empty.")
        if length > 25 * 1024 * 1024:
            raise VisionRequestError("body_too_large", "Request body exceeds 25 MB.", status=413)

        return self.rfile.read(length)

    def _read_chunked_body(self) -> bytes:
        chunks: list[bytes] = []
        total_length = 0
        while True:
            size_line = self.rfile.readline(128)
            if not size_line or not size_line.endswith(b"\r\n"):
                raise VisionRequestError("invalid_chunked_body", "Chunked request is missing a chunk size.")
            try:
                chunk_length = int(size_line[:-2].split(b";", 1)[0], 16)
            except ValueError as exc:
                raise VisionRequestError("invalid_chunked_body", "Chunked request has an invalid chunk size.") from exc
            if chunk_length < 0 or total_length + chunk_length > 25 * 1024 * 1024:
                raise VisionRequestError("body_too_large", "Request body exceeds 25 MB.", status=413)
            if chunk_length == 0:
                while True:
                    trailer = self.rfile.readline(8 * 1024)
                    if trailer in {b"", b"\r\n"}:
                        return b"".join(chunks)
            chunk = self.rfile.read(chunk_length)
            if len(chunk) != chunk_length or self.rfile.read(2) != b"\r\n":
                raise VisionRequestError("invalid_chunked_body", "Chunked request ended unexpectedly.")
            chunks.append(chunk)
            total_length += chunk_length

    def _read_json_body(self) -> Dict[str, Any]:
        raw_body = self._read_body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VisionRequestError("invalid_json", "Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise VisionRequestError("invalid_json", "Request JSON must be an object.")
        return payload

    def _read_multipart_body(self, content_type: str) -> Dict[str, Any]:
        raw_body = self._read_body()
        message_bytes = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + raw_body
        )
        try:
            message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        except Exception as exc:
            raise VisionRequestError("invalid_multipart", "Multipart form data could not be parsed.") from exc
        if not message.is_multipart():
            raise VisionRequestError("invalid_multipart", "Multipart form data must include a boundary.")

        question: str | None = None
        image_bytes: bytes | None = None
        mime_type: str | None = None
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            name = part.get_param("name", header="content-disposition")
            body = part.get_payload(decode=True) or b""
            if name == "question":
                try:
                    question = body.decode(part.get_content_charset() or "utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise VisionRequestError("invalid_question", "question must be UTF-8 text.") from exc
            elif name == "file":
                image_bytes = body
                mime_type = part.get_content_type()

        if not image_bytes:
            raise VisionRequestError("missing_image", "Multipart request must include a non-empty file field.")

        device_id = self.headers.get("Device-Id")
        session_id = self.headers.get("X-Session-Id") or self.headers.get("Client-Id")
        payload: Dict[str, Any] = {
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": mime_type or "image/jpeg",
        }
        if question:
            payload["prompt"] = question
        if device_id:
            payload["device_id"] = device_id
        if session_id:
            payload["session_id"] = session_id
        return payload

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str, port: int, config: VisionHttpConfig) -> VisionHttpServer:
    return VisionHttpServer((host, port), VisionRequestHandler, config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Deskbot vision HTTP service.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument(
        "--port",
        type=int,
        default=8005,
        help="HTTP bind port. 8005 avoids the voice server OTA/health service on 8003.",
    )
    parser.add_argument("--provider", choices=["local", "zhipu", "auto"], default="auto")
    parser.add_argument("--env-file", help="Optional .env path. Defaults to .env in the current directory.")
    parser.add_argument("--retries", type=int, default=0, help="Cloud-provider retry count.")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="Seconds between cloud-provider retries.")
    parser.add_argument("--upload-dir", default="build/vision-http", help="Directory for base64 image uploads.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Default prompt when a request omits one.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = VisionHttpConfig(
        provider=args.provider,
        env_file=args.env_file,
        retries=args.retries,
        retry_delay=args.retry_delay,
        upload_dir=args.upload_dir,
        default_prompt=args.prompt,
    )
    server = create_server(args.host, args.port, config)
    print(f"Deskbot vision HTTP service listening on http://{args.host}:{args.port}/mcp/vision/explain")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeskbot vision HTTP service stopped.")
    finally:
        server.server_close()
    return 0


def _image_from_payload(payload: Dict[str, Any], request_id: str, upload_dir: str) -> Path:
    image_path = payload.get("image_path")
    image_base64 = payload.get("image_base64")

    if image_path:
        path = Path(str(image_path))
        if not path.exists():
            raise VisionRequestError("image_not_found", f"Image does not exist: {path}", status=404)
        if not path.is_file():
            raise VisionRequestError("image_not_file", f"Image path is not a file: {path}")
        return path

    if image_base64:
        raw_text = str(image_base64)
        mime_type = str(payload.get("mime_type") or "image/jpeg")
        if raw_text.startswith("data:"):
            prefix, _, raw_text = raw_text.partition(",")
            if prefix.startswith("data:") and ";" in prefix:
                mime_type = prefix[5:].split(";", 1)[0] or mime_type
        try:
            image_bytes = base64.b64decode(raw_text, validate=True)
        except Exception as exc:
            raise VisionRequestError("invalid_image_base64", "image_base64 must be valid base64 data.") from exc
        if not image_bytes:
            raise VisionRequestError("empty_image", "Decoded image data is empty.")

        detect_image_info(image_bytes)
        output = Path(upload_dir) / f"{request_id}{_extension_for_mime(mime_type)}"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image_bytes)
        return output

    raise VisionRequestError("missing_image", "Provide either image_path or image_base64.")


def _request_id(payload: Dict[str, Any]) -> str:
    value = str(payload.get("request_id") or "").strip()
    return value or uuid.uuid4().hex


def _safe_request_id(payload: Dict[str, Any]) -> Optional[str]:
    value = payload.get("request_id") if isinstance(payload, dict) else None
    if value is None:
        return None
    return str(value)


def _success_envelope(request_id: str, payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    event = _base_event("vision.analysis.completed", request_id, payload)
    event["result"] = result
    return {
        "schema_version": HTTP_SCHEMA_VERSION,
        "request_id": request_id,
        "status": "ok",
        "result": result,
        "event": event,
        "protocol_event": _protocol_event(event, request_id),
        "errors": [],
    }


def _error_envelope(
    request_id: Optional[str],
    code: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    safe_payload = payload or {}
    event = _base_event("vision.analysis.failed", request_id, safe_payload)
    event["error"] = {"code": code, "message": message}
    return {
        "schema_version": HTTP_SCHEMA_VERSION,
        "request_id": request_id,
        "status": "error",
        "result": None,
        "event": event,
        "protocol_event": _protocol_event(event, request_id),
        "errors": [{"code": code, "message": message}],
    }


def _base_event(event_type: str, request_id: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": event_type,
        "source": "vision-service",
        "target": "dispatcher",
        "request_id": request_id,
        "device_id": payload.get("device_id"),
        "session_id": payload.get("session_id"),
    }


def _protocol_event(event: Dict[str, Any], request_id: Optional[str]) -> Dict[str, Any]:
    """Wrap a vision outcome in the shared Deskbot control-message envelope."""

    event_id = f"vision-{request_id or uuid.uuid4().hex}"
    envelope: Dict[str, Any] = {
        "version": "0.1",
        "type": "vision.event",
        "id": event_id,
        "timestamp_ms": int(time.time() * 1000),
        "payload": {
            "name": event["type"],
            "source": event["source"],
            "target": event["target"],
            "device_id": event["device_id"],
            "session_id": event["session_id"],
            **({"result": event["result"]} if "result" in event else {}),
            **({"error": event["error"]} if "error" in event else {}),
        },
    }
    if request_id:
        envelope["correlation_id"] = request_id
    return envelope


def _extension_for_mime(mime_type: str) -> str:
    normalized = mime_type.lower().split(";", 1)[0].strip()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/x-portable-pixmap": ".ppm",
    }.get(normalized, ".img")


if __name__ == "__main__":
    raise SystemExit(main())
