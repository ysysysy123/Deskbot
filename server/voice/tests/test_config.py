from pathlib import Path

import pytest

from voice_server.config import ConfigError, load_config


def test_environment_overrides_llm_secret(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  base_url: http://127.0.0.1:11434/v1\n  model: qwen2.5\n", encoding="utf-8")
    config = load_config(path, {"VOICE_LLM_API_KEY": "secret-value"})
    assert config.llm.api_key == "secret-value"
    assert config.server.ws_port == 8000


def test_non_loopback_admin_requires_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("admin_api:\n  host: 0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="memory admin token"):
        load_config(path, {})


def test_non_loopback_admin_rejects_whitespace_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("admin_api:\n  host: 0.0.0.0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="memory admin token"):
        load_config(path, {"VOICE_MEMORY_ADMIN_TOKEN": "   "})


def test_protocol_invariants_cannot_be_changed(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  input_sample_rate: 8000\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="16000"):
        load_config(path, {})


def test_unknown_configuration_key_is_rejected(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  unknown_option: true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown configuration key: llm.unknown_option"):
        load_config(path, {})


def test_bearer_auth_requires_a_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("auth:\n  mode: bearer\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="auth token"):
        load_config(path, {})


def test_bearer_auth_rejects_whitespace_token(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("auth:\n  mode: bearer\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="auth token"):
        load_config(path, {"VOICE_AUTH_TOKEN": " \t"})


def test_allowlist_auth_requires_devices(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("auth:\n  mode: allowlist\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="allowed_devices"):
        load_config(path, {})


def test_invalid_port_is_rejected(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("server:\n  ws_port: 65536\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="server.ws_port"):
        load_config(path, {})


def test_vad_silence_threshold_cannot_exceed_speech_threshold(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "vad:\n  speech_threshold: 0.2\n  silence_threshold: 0.3\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="silence_threshold"):
        load_config(path, {})


def test_empty_llm_model_is_rejected(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  model: ''\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="llm.model"):
        load_config(path, {})


def test_mixed_type_unknown_root_keys_raise_config_error(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("1: invalid\nunexpected: invalid\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown configuration section"):
        load_config(path, {})


def test_mixed_type_unknown_section_keys_raise_config_error(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  1: invalid\n  unexpected: invalid\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown configuration key"):
        load_config(path, {})


@pytest.mark.parametrize(
    ("section", "field"),
    (("llm", "api_key"), ("auth", "token"), ("admin_api", "token")),
)
def test_yaml_secret_fields_are_rejected(tmp_path: Path, section: str, field: str):
    path = tmp_path / "config.yaml"
    path.write_text(f"{section}:\n  {field}: yaml-value\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=f"{section}.{field} must be supplied through environment"):
        load_config(path, {})


@pytest.mark.parametrize(
    ("config_text", "field"),
    (
        ("server:\n  hello_timeout_s: .nan\n", "server.hello_timeout_s"),
        ("asr:\n  timeout_s: .inf\n", "asr.timeout_s"),
    ),
)
def test_non_finite_float_configuration_is_rejected(
    tmp_path: Path, config_text: str, field: str
):
    path = tmp_path / "config.yaml"
    path.write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match=f"{field} must be finite"):
        load_config(path, {})
