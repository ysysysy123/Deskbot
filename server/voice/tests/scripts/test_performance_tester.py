import json
from pathlib import Path
import subprocess
import sys
import wave

import pytest

from voice_server.config import AppConfig, LlmConfig
from voice_server import performance


def make_wav(path):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)


async def test_all_modules_reuse_provider_and_measure_real_output(monkeypatch, tmp_path):
    class ASR:
        async def transcribe(self, pcm, rate):
            assert len(pcm) == 32000
            assert rate == 16000
            return "识别结果"

    class LLM:
        closed = False

        async def stream(self, messages):
            assert messages[-1]["content"] == "你好"
            yield ""
            yield "测试"
            yield "回复"

        async def close(self):
            self.closed = True

    class TTS:
        async def synthesize(self, text):
            yield b"\0\0" * 24000

    providers = {"asr": ASR(), "llm": LLM(), "tts": TTS()}
    created = []

    def factory(kind, config):
        created.append(kind)
        return providers[kind]

    monkeypatch.setattr(performance, "create_provider", factory)
    audio = tmp_path / "sample.wav"
    make_wav(audio)
    report = await performance.run_benchmarks(AppConfig(), kinds=["asr", "llm", "tts"], repeat=2, text="你好", audio=audio)

    assert report["ok"]
    assert created == ["asr", "llm", "tts"]
    assert providers["llm"].closed
    asr, llm, tts = report["results"]
    assert asr["runs"][0]["text"] == "识别结果"
    assert asr["runs"][0]["audio_seconds"] == 1
    assert asr["runs"][0]["first_response_ms"] == asr["runs"][0]["total_ms"]
    assert llm["runs"][0]["text"] == "测试回复"
    assert llm["runs"][0]["chunks"] == 2
    assert tts["runs"][0]["audio_seconds"] == 1
    for result in report["results"]:
        assert len(result["runs"]) == 2
        assert 0 <= result["mean_first_response_ms"] <= result["mean_total_ms"]


async def test_empty_llm_stream_fails_and_closes_provider(monkeypatch):
    class EmptyLLM:
        closed = False

        async def stream(self, messages):
            yield ""

        async def close(self):
            self.closed = True

    provider = EmptyLLM()
    monkeypatch.setattr(performance, "create_provider", lambda kind, config: provider)
    report = await performance.run_benchmarks(AppConfig(), kinds=["llm"], repeat=3, text="你好", audio=None)
    assert not report["ok"]
    assert report["results"][0]["error"] == "ValueError"
    assert not report["results"][0]["runs"]
    assert provider.closed


async def test_timeout_closes_stream_and_does_not_claim_success(monkeypatch):
    import asyncio

    class SlowLLM:
        stream_closed = False
        closed = False

        async def stream(self, messages):
            try:
                yield "开头"
                await asyncio.sleep(10)
            finally:
                self.stream_closed = True

        async def close(self):
            self.closed = True

    provider = SlowLLM()
    monkeypatch.setattr(performance, "create_provider", lambda kind, config: provider)
    config = AppConfig(llm=LlmConfig(timeout_s=0.01))
    report = await performance.run_benchmarks(config, kinds=["llm"], repeat=1, text="你好", audio=None)
    assert not report["ok"]
    assert report["results"][0]["error"] == "TimeoutError"
    assert provider.stream_closed and provider.closed


def test_cli_help_needs_no_model_dependencies():
    result = subprocess.run([sys.executable, "performance_tester.py", "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--repeat" in result.stdout
    assert "--json" in result.stdout


def test_cli_json_failure_has_nonzero_exit_and_does_not_expose_provider_error(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")

    def failing_provider(kind, config):
        raise RuntimeError("private-credential-in-upstream-url")

    monkeypatch.setattr(performance, "create_provider", failing_provider)
    code = performance.main(["llm", "--config", str(config), "--repeat", "1", "--json", "-"])
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert code == 1
    assert not report["ok"]
    assert "private-credential" not in output.out + output.err


def test_asr_missing_weights_fails_before_import_or_download(tmp_path):
    from voice_server.config import AsrConfig

    with pytest.raises(FileNotFoundError):
        performance.create_provider("asr", AppConfig(asr=AsrConfig(model_path=str(tmp_path))))
