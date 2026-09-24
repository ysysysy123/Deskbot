from pathlib import Path

import pytest

from voice_server.config import ConfigError, load_config


def test_camera_and_vision_settings_load_from_dotenv(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "VOICE_MCP_ENABLED=true\nVOICE_MCP_URL=http://127.0.0.1:9006/mcp\n"
        "VOICE_VISION_ENABLED=true\nVOICE_VISION_MODEL=glm-4v-flash\n"
        "VOICE_VISION_API_KEY=test-vision-secret\n", encoding="utf-8",
    )
    config = load_config(path, {})
    assert config.mcp.enabled is True
    assert config.mcp.url == "http://127.0.0.1:9006/mcp"
    assert config.vision.enabled is True
    assert config.vision.model == "glm-4v-flash"
    assert config.vision.api_key == "test-vision-secret"


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


def test_dialogue_settings_and_memory_window_load_from_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("dialogue:\n  system_prompt: 你是小桌\n  sentence_queue_size: 3\nmemory:\n  context_limit: 32\n", encoding="utf-8")
    config = load_config(path, {})
    assert config.dialogue.system_prompt == "你是小桌"
    assert config.dialogue.sentence_queue_size == 3
    assert config.memory.context_limit == 32


def test_context_cap_cannot_drop_messages_before_summary_threshold(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("memory:\n  context_limit: 10\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="context_limit"):
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
