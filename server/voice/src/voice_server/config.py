from __future__ import annotations

import ipaddress
import math
import os
from dataclasses import dataclass, fields, field
from pathlib import Path
from typing import Any, Mapping, TypeVar

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    ws_port: int = 8000
    ota_host: str = "0.0.0.0"
    ota_port: int = 8003
    public_websocket_url: str = ""
    hello_timeout_s: float = 10.0
    idle_timeout_s: float = 120.0
    max_text_bytes: int = 16_384
    max_binary_bytes: int = 65_536
    max_recording_bytes: int = 2_000_000
    max_recording_seconds: float = 30.0


@dataclass(frozen=True)
class AudioConfig:
    input_sample_rate: int = 16_000
    output_sample_rate: int = 24_000
    channels: int = 1
    frame_duration_ms: int = 60


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "none"
    token: str = ""
    allowed_devices: tuple[str, ...] = ()
    ota_include_token: bool = False


@dataclass(frozen=True)
class AdminApiConfig:
    host: str = "127.0.0.1"
    port: int = 8004
    token: str = ""


@dataclass(frozen=True)
class VadConfig:
    model_path: str = "models/snakers4_silero-vad"
    speech_threshold: float = 0.5
    silence_threshold: float = 0.3
    min_silence_duration_ms: int = 600


@dataclass(frozen=True)
class AsrConfig:
    model_path: str = "models/SenseVoiceSmall"
    max_concurrency: int = 1
    timeout_s: float = 30.0


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    summary_max_tokens: int = 256
    timeout_s: float = 60.0


@dataclass(frozen=True)
class TtsConfig:
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    timeout_s: float = 30.0
    error_text: str = "抱歉，服务暂时不可用。"


@dataclass(frozen=True)
class MusicConfig:
    enabled: bool = True
    ffmpeg_path: str = "ffmpeg"
    max_duration_s: int = 300
    search_timeout_s: float = 20.0


@dataclass(frozen=True)
class MemoryConfig:
    database_path: str = "data/memory.db"
    recent_limit: int = 10
    summary_threshold: int = 12


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    admin_api: AdminApiConfig = field(default_factory=AdminApiConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


ConfigSection = TypeVar("ConfigSection")
_SECRET_FIELDS = {("llm", "api_key"), ("auth", "token"), ("admin_api", "token")}


def load_config(path: Path, environ: Mapping[str, str] = os.environ) -> AppConfig:
    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"unable to read configuration: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigError("invalid YAML configuration") from error

    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, Mapping):
        raise ConfigError("configuration root must be a mapping")

    section_types = {
        "server": ServerConfig,
        "audio": AudioConfig,
        "auth": AuthConfig,
        "admin_api": AdminApiConfig,
        "vad": VadConfig,
        "asr": AsrConfig,
        "llm": LlmConfig,
        "tts": TtsConfig,
        "music": MusicConfig,
        "memory": MemoryConfig,
    }
    unknown_sections = set(raw_config) - set(section_types)
    if unknown_sections:
        raise ConfigError(f"unknown configuration section: {sorted(unknown_sections, key=str)[0]}")

    sections = {
        name: _load_section(name, section_type, raw_config.get(name, {}))
        for name, section_type in section_types.items()
    }
    sections["llm"] = _override(sections["llm"], "api_key", environ.get("VOICE_LLM_API_KEY"))
    sections["auth"] = _override(sections["auth"], "token", environ.get("VOICE_AUTH_TOKEN"))
    sections["admin_api"] = _override(
        sections["admin_api"], "token", environ.get("VOICE_MEMORY_ADMIN_TOKEN")
    )

    config = AppConfig(**sections)
    _validate(config)
    return config


def _load_section(name: str, section_type: type[ConfigSection], raw: Any) -> ConfigSection:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{name} must be a mapping")

    defaults = section_type()
    valid_keys = {item.name for item in fields(defaults)}
    unknown_keys = set(raw) - valid_keys
    if unknown_keys:
        raise ConfigError(f"unknown configuration key: {name}.{sorted(unknown_keys, key=str)[0]}")

    values = {item.name: getattr(defaults, item.name) for item in fields(defaults)}
    for key, value in raw.items():
        if (name, key) in _SECRET_FIELDS:
            raise ConfigError(f"{name}.{key} must be supplied through environment")
        values[key] = _coerce_value(name, key, getattr(defaults, key), value)
    return section_type(**values)


def _coerce_value(section: str, key: str, default: Any, value: Any) -> Any:
    label = f"{section}.{key}"
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{label} must be a boolean")
        return value
    if isinstance(default, int):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{label} must be an integer")
        return value
    if isinstance(default, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(f"{label} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise ConfigError(f"{label} must be finite")
        return number
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"{label} must be a string")
        return value
    if isinstance(default, tuple):
        if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"{label} must be a list of strings")
        return tuple(value)
    raise ConfigError(f"unsupported configuration value: {label}")


def _override(section: ConfigSection, key: str, value: str | None) -> ConfigSection:
    if value is None:
        return section
    values = {item.name: getattr(section, item.name) for item in fields(section)}
    values[key] = value
    return type(section)(**values)


def _validate(config: AppConfig) -> None:
    for name, value in (
        ("server.ws_port", config.server.ws_port),
        ("server.ota_port", config.server.ota_port),
        ("admin_api.port", config.admin_api.port),
    ):
        if not 1 <= value <= 65_535:
            raise ConfigError(f"{name} must be between 1 and 65535")

    expected_audio = {
        "input_sample_rate": 16_000,
        "output_sample_rate": 24_000,
        "channels": 1,
        "frame_duration_ms": 60,
    }
    for name, expected in expected_audio.items():
        if getattr(config.audio, name) != expected:
            raise ConfigError(f"audio.{name} must be {expected}")

    for name, value in (
        ("server.hello_timeout_s", config.server.hello_timeout_s),
        ("server.idle_timeout_s", config.server.idle_timeout_s),
        ("server.max_text_bytes", config.server.max_text_bytes),
        ("server.max_binary_bytes", config.server.max_binary_bytes),
        ("server.max_recording_bytes", config.server.max_recording_bytes),
        ("server.max_recording_seconds", config.server.max_recording_seconds),
        ("vad.min_silence_duration_ms", config.vad.min_silence_duration_ms),
        ("asr.max_concurrency", config.asr.max_concurrency),
        ("asr.timeout_s", config.asr.timeout_s),
        ("llm.max_tokens", config.llm.max_tokens),
        ("llm.summary_max_tokens", config.llm.summary_max_tokens),
        ("llm.timeout_s", config.llm.timeout_s),
        ("tts.timeout_s", config.tts.timeout_s),
        ("music.max_duration_s", config.music.max_duration_s),
        ("music.search_timeout_s", config.music.search_timeout_s),
        ("memory.recent_limit", config.memory.recent_limit),
        ("memory.summary_threshold", config.memory.summary_threshold),
    ):
        if value <= 0:
            raise ConfigError(f"{name} must be positive")

    for name, value in (
        ("vad.speech_threshold", config.vad.speech_threshold),
        ("vad.silence_threshold", config.vad.silence_threshold),
    ):
        if not 0 <= value <= 1:
            raise ConfigError(f"{name} must be between 0 and 1")
    if config.vad.silence_threshold > config.vad.speech_threshold:
        raise ConfigError("vad.silence_threshold cannot exceed vad.speech_threshold")

    if config.auth.mode not in {"none", "allowlist", "bearer"}:
        raise ConfigError("auth.mode must be none, allowlist, or bearer")
    if config.auth.mode == "allowlist" and not config.auth.allowed_devices:
        raise ConfigError("auth.allowed_devices is required for allowlist mode")
    if config.auth.mode == "bearer" and not config.auth.token.strip():
        raise ConfigError("auth token is required for bearer mode")

    if not config.llm.base_url:
        raise ConfigError("llm.base_url is required")
    if not config.llm.model:
        raise ConfigError("llm.model is required")

    if not _is_loopback(config.admin_api.host) and not config.admin_api.token.strip():
        raise ConfigError("memory admin token is required for a non-loopback admin host")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
