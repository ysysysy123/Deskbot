import os
import subprocess
import sys
from pathlib import Path

from voice_server.config import load_config
from voice_server.environment import load_environment


def test_dotenv_loads_next_to_config_and_converts_config_types(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  model: yaml-model\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "VOICE_LLM_MODEL=env-model\nVOICE_LLM_API_KEY='test-${literal}'\n"
        "VOICE_SERVER_WS_PORT=8123\nVOICE_MUSIC_ENABLED=false\n"
        "VOICE_AUTH_ALLOWED_DEVICES='[device-a, device-b]'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(config_path, {})
    assert config.llm.model == "env-model"
    assert config.llm.api_key == "test-${literal}"
    assert config.server.ws_port == 8123
    assert config.music.enabled is False
    assert config.auth.allowed_devices == ("device-a", "device-b")


def test_exported_values_override_dotenv_including_legacy_aliases(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "VOICE_LLM_MODEL=local\nVOICE_MUSIC_NETEASE_API_URL=https://local.example\n"
        "VOICE_ADMIN_API_TOKEN=local-token\n",
        encoding="utf-8",
    )
    config = load_config(path, {
        "VOICE_LLM_MODEL": "exported",
        "NETEASE_API_URL": "https://exported.example",
        "VOICE_MEMORY_ADMIN_TOKEN": "exported-token",
    })
    assert config.llm.model == "exported"
    assert config.music.netease_api_url == "https://exported.example"
    assert config.admin_api.token == "exported-token"


def test_runtime_dotenv_reaches_cookie_and_path_consumers(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "NETEASE_COOKIE='example=value; second=value'\n"
        "VOICE_OPUS_DLL_DIR='D:\\voice\\Library\\bin'\nFFMPEG=/opt/voice/ffmpeg\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert os.environ["NETEASE_COOKIE"] == "example=value; second=value"
    assert os.environ["VOICE_OPUS_DLL_DIR"] == r"D:\voice\Library\bin"
    assert config.music.ffmpeg_path == "/opt/voice/ffmpeg"


def test_load_environment_preserves_exported_values_and_accepts_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", {"NETEASE_COOKIE": "exported"})
    path = tmp_path / ".env"
    assert load_environment(path) == {}
    path.write_text("NETEASE_COOKIE=local\nMUSIC_PORT=8120\n", encoding="utf-8")
    load_environment(path)
    assert os.environ["NETEASE_COOKIE"] == "exported"
    assert os.environ["MUSIC_PORT"] == "8120"


def test_cli_import_waits_for_dotenv_before_loading_opus(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    dll_path = str(tmp_path / "opus")
    (tmp_path / ".env").write_text(f"VOICE_OPUS_DLL_DIR='{dll_path}'\n", encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("VOICE_OPUS_DLL_DIR", None)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", """
import os, sys
from pathlib import Path
import voice_server.__main__
assert 'voice_server.audio.opus' not in sys.modules
from voice_server.config import load_config
load_config(Path(sys.argv[1]))
import voice_server.audio.opus
assert os.environ['PATH'].startswith(sys.argv[2] + os.pathsep)
""", str(config_path), dll_path],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_launcher_help_works_without_pythonpath():
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    launcher = Path(__file__).parents[1] / "run.py"
    result = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
