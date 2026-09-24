from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias


class ProtocolError(ValueError):
    close_code = 1002


@dataclass(frozen=True)
class HelloMessage:
    version: int
    format: str
    sample_rate: int
    channels: int
    frame_duration: int
    local_test_turn_events: bool = False


@dataclass(frozen=True)
class ListenMessage:
    state: str
    mode: str | None
    text: str | None


@dataclass(frozen=True)
class AbortMessage:
    pass


@dataclass(frozen=True)
class PingMessage:
    nonce: str | None = None


ClientMessage: TypeAlias = HelloMessage | ListenMessage | AbortMessage | PingMessage

_HELLO_REQUIRED_FIELDS = {"type", "version", "transport", "audio_params"}
_HELLO_OPTIONAL_FIELDS = {"features"}
_AUDIO_PARAMS_FIELDS = {"format", "sample_rate", "channels", "frame_duration"}
_LISTEN_REQUIRED_FIELDS = {"type", "state"}
_LISTEN_OPTIONAL_FIELDS = {"mode", "text", "session_id"}
_ABORT_OPTIONAL_FIELDS = {"session_id", "reason"}
_TTS_STATES = {"start", "sentence_start", "stop"}


def parse_client_message(raw: str) -> ClientMessage:
    try:
        message = json.loads(raw, object_pairs_hook=_reject_duplicate_members)
    except (TypeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid JSON message") from error
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")

    message_type = message.get("type")
    if message_type == "hello":
        return _parse_hello(message)
    if message_type == "listen":
        return _parse_listen(message)
    if message_type == "abort":
        _require_allowed_fields(message, {"type"}, _ABORT_OPTIONAL_FIELDS)
        _validate_optional_string(message, "session_id")
        _validate_optional_string(message, "reason")
        return AbortMessage()
    if message_type == "ping":
        _require_allowed_fields(message, {"type"}, {"timestamp", "nonce"})
        _validate_optional_string(message, "nonce")
        return PingMessage(nonce=message.get("nonce"))
    raise ProtocolError("unsupported message type")


def make_server_hello(session_id: str, *, local_test_turn_events: bool = False) -> dict[str, object]:
    result = {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    if local_test_turn_events:
        result["features"] = {"local_test_turn_events": True}
    return result


def make_stt(session_id: str, text: str) -> dict[str, str]:
    return {"session_id": session_id, "type": "stt", "text": text}


def make_tts(session_id: str, state: str, text: str | None = None) -> dict[str, str]:
    if state not in _TTS_STATES:
        raise ValueError("unsupported TTS state")
    if state == "sentence_start":
        if not isinstance(text, str):
            raise ValueError("sentence_start TTS requires text")
        return {"session_id": session_id, "type": "tts", "state": state, "text": text}
    if text is not None:
        raise ValueError(f"{state} TTS does not accept text")
    return {"session_id": session_id, "type": "tts", "state": state}


def make_llm(session_id: str, text: str, emotion: str) -> dict[str, str]:
    return {"session_id": session_id, "type": "llm", "text": text, "emotion": emotion}


def _parse_hello(message: dict[str, object]) -> HelloMessage:
    _require_allowed_fields(message, _HELLO_REQUIRED_FIELDS, _HELLO_OPTIONAL_FIELDS)
    features = message.get("features", {})
    if not isinstance(features, dict):
        raise ProtocolError("features must be an object")
    local_test_turn_events = features.get("local_test_turn_events", False)
    if not isinstance(local_test_turn_events, bool):
        raise ProtocolError("local_test_turn_events must be a boolean")
    audio_params = message["audio_params"]
    if not isinstance(audio_params, dict):
        raise ProtocolError("audio_params must be an object")
    _require_fields(audio_params, _AUDIO_PARAMS_FIELDS)
    if (
        not _is_int(message["version"])
        or message["version"] != 1
        or message["transport"] != "websocket"
        or not all(_is_int(audio_params[field]) for field in ("sample_rate", "channels", "frame_duration"))
        or audio_params != {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        }
    ):
        raise ProtocolError("unsupported hello audio_params or transport")
    return HelloMessage(
        version=1,
        format="opus",
        sample_rate=16000,
        channels=1,
        frame_duration=60,
        local_test_turn_events=local_test_turn_events,
    )


def _parse_listen(message: dict[str, object]) -> ListenMessage:
    _require_allowed_fields(message, _LISTEN_REQUIRED_FIELDS, _LISTEN_OPTIONAL_FIELDS)
    state = message["state"]
    mode = message.get("mode")
    text = message.get("text")
    if not isinstance(state, str) or text is not None and not isinstance(text, str):
        raise ProtocolError("invalid listen values")
    _validate_optional_string(message, "session_id")
    if state not in {"start", "stop", "detect"}:
        raise ProtocolError("unsupported listen state or mode")
    if state == "start" or "mode" in message:
        if not isinstance(mode, str) or mode not in {"manual", "auto", "realtime"}:
            raise ProtocolError("unsupported listen mode")
    return ListenMessage(state=state, mode=mode, text=text)


def _require_fields(message: dict[str, object], expected: set[str]) -> None:
    if set(message) != expected:
        raise ProtocolError("invalid message fields")


def _require_allowed_fields(
    message: dict[str, object], required: set[str], optional: set[str]
) -> None:
    if not required <= set(message) or set(message) - (required | optional):
        raise ProtocolError("invalid message fields")


def _validate_optional_string(message: dict[str, object], field: str) -> None:
    if field in message and not isinstance(message[field], str):
        raise ProtocolError(f"{field} must be a string")


def _reject_duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    message: dict[str, object] = {}
    for key, value in pairs:
        if key in message:
            raise ProtocolError("duplicate JSON member")
        message[key] = value
    return message


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
