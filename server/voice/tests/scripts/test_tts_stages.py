import importlib.util
from pathlib import Path


def test_stage_statistics_exclude_warmups_and_count_failures(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("benchmark_tts_stages", scripts / "benchmark_tts_stages.py")
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    runs = [
        {"mode": "default", "warmup": True, "metrics": {"status": "success", "first_pcm_ms": 9999}},
        {"mode": "default", "warmup": False, "metrics": {"status": "success", "first_pcm_ms": 100}},
        {"mode": "default", "warmup": False, "metrics": {"status": "error", "first_pcm_ms": 10000}},
        {"mode": "default", "warmup": False, "metrics": {"status": "success", "first_pcm_ms": 300}},
    ]
    result = benchmark.summarize(runs)
    assert result["default"]["measured"] == 3
    assert result["default"]["successful"] == 2
    assert result["default"]["failed"] == 1
    assert result["default"]["metrics"]["first_pcm_ms"] == {"n": 2, "p50": 200, "p95": 290}
    assert result["small_reads"]["metrics"] == {}
