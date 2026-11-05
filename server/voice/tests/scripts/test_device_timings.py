import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def summarize(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("summarize_device_timings", scripts / "summarize_device_timings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summarize


def test_device_intervals_keep_interleaved_turns_and_their_clocks_separate(summarize):
    report = summarize("""
I App: unrelated sensitive log text
I App: Voice timing: turn=a:1 event=first_output generation=3 voice_end_us=1000000 received_us=1300000 output_us=1400000
I App: Voice timing: turn=b:1 event=first_output generation=1 voice_end_us=10000 received_us=30000 output_us=40000
I App: Voice timing: turn=a:1 event=barge_in detected_us=1500000 reset_us=1510000
I App: Voice timing: turn=a:1 event=drained generation=3 drained_us=1600000
""")
    a, b = report["turns"]
    assert a["eou_to_output_ms"] == 400
    assert a["first_audio_to_output_ms"] == 100
    assert a["output_to_last_drain_ms"] == 200
    assert a["vad_to_queue_reset_ms"] == 10
    assert b["eou_to_output_ms"] == 30
    assert report["summary_ms"]["eou_to_output_ms"] == {"n": 2, "p50": 215, "p95": 381.5}
    assert report["summary_ms"]["vad_to_queue_reset_ms"]["n"] == 1
    assert "sensitive" not in str(report)


def test_manual_turn_without_vad_anchor_is_not_counted_as_zero_latency(summarize):
    report = summarize("I App: Voice timing: turn=manual:1 event=first_output generation=2 voice_end_us=0 received_us=40000 output_us=90000")
    assert "eou_to_output_ms" not in report["turns"][0]
    assert "eou_to_output_ms" not in report["summary_ms"]
    assert report["summary_ms"]["first_audio_to_output_ms"]["p50"] == 50
