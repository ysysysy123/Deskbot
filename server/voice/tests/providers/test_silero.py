import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from voice_server.providers.silero import SileroVADProvider


def pcm_samples(value: int, count: int) -> bytes:
    return struct.pack("<h", value) * count


class FakeONNXSession:
    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = iter(probabilities)
        self.calls: list[dict[str, np.ndarray]] = []

    def run(self, _output_names, inputs):
        self.calls.append({name: value.copy() for name, value in inputs.items()})
        state = np.full((2, 1, 128), len(self.calls), dtype=np.float32)
        return [np.array([[next(self._probabilities)]], dtype=np.float32), state]


async def test_silero_keeps_buffer_context_and_state_per_provider():
    """Would fail if separate connections shared streaming VAD state."""
    first_session = FakeONNXSession([0.8, 0.8])
    second_session = FakeONNXSession([0.8])
    first = SileroVADProvider("unused", inference_session=first_session)
    second = SileroVADProvider("unused", inference_session=second_session)

    assert not await first.is_speech(pcm_samples(1000, 511), 16000)
    assert await second.is_speech(pcm_samples(2000, 512), 16000)
    assert await first.is_speech(pcm_samples(1000, 1), 16000)
    assert await first.is_speech(pcm_samples(3000, 512), 16000)

    assert len(first_session.calls) == 2
    assert len(second_session.calls) == 1
    assert np.allclose(first_session.calls[0]["input"][0, :64], 0.0)
    assert np.allclose(second_session.calls[0]["input"][0, :64], 0.0)
    assert np.allclose(first_session.calls[1]["input"][0, :64], 1000 / 32768)
    assert np.allclose(first_session.calls[1]["state"], 1.0)
    assert np.allclose(second_session.calls[0]["state"], 0.0)
    assert first_session.calls[0]["sr"].item() == 16000


async def test_silero_applies_inclusive_threshold_hysteresis():
    """Would fail if threshold boundaries or the hold band chose the wrong state."""
    provider = SileroVADProvider("unused", inference_session=FakeONNXSession([0.5, 0.4, 0.3]))

    assert await provider.is_speech(pcm_samples(0, 512), 16000)
    assert await provider.is_speech(pcm_samples(0, 512), 16000)
    assert not await provider.is_speech(pcm_samples(0, 512), 16000)


async def test_silero_keeps_last_speech_result_per_provider():
    """Would fail if one connection's speech decision changed another connection."""
    speech = SileroVADProvider("unused", inference_session=FakeONNXSession([0.8]))
    silence = SileroVADProvider("unused", inference_session=FakeONNXSession([0.2]))

    assert await speech.is_speech(pcm_samples(0, 512), 16000)
    assert not await silence.is_speech(pcm_samples(0, 512), 16000)
    assert await speech.is_speech(b"", 16000)


async def test_silero_rejects_wrong_sample_rate():
    """Would fail if the 16 kHz VAD model received incompatible audio."""
    provider = SileroVADProvider("unused", inference_session=FakeONNXSession([]))

    with pytest.raises(ValueError, match="16000"):
        await provider.is_speech(pcm_samples(0, 512), 8000)


async def test_silero_rejects_incomplete_int16_sample():
    """Would fail if malformed PCM exposed a low-level NumPy buffer error."""
    provider = SileroVADProvider("unused", inference_session=FakeONNXSession([]))

    with pytest.raises(ValueError, match="even number of bytes"):
        await provider.is_speech(b"\x00", 16000)


async def test_silero_sends_expected_onnx_tensor_shapes_and_dtypes():
    """Would fail if ONNX inputs no longer matched the local Silero model contract."""
    session = FakeONNXSession([0.8])
    provider = SileroVADProvider("unused", inference_session=session)

    await provider.is_speech(pcm_samples(1000, 512), 16000)

    inputs = session.calls[0]
    assert inputs["input"].shape == (1, 576)
    assert inputs["input"].dtype == np.float32
    assert inputs["state"].shape == (2, 1, 128)
    assert inputs["state"].dtype == np.float32
    assert inputs["sr"].shape == ()
    assert inputs["sr"].dtype == np.int64


async def test_silero_consumes_all_complete_windows_from_one_pcm_chunk():
    """Would fail if a large network chunk discarded or deferred a full VAD window."""
    session = FakeONNXSession([0.8, 0.8, 0.8])
    provider = SileroVADProvider("unused", inference_session=session)

    assert await provider.is_speech(pcm_samples(0, 1024), 16000)
    assert len(session.calls) == 2
    assert await provider.is_speech(pcm_samples(0, 511), 16000)
    assert len(session.calls) == 2
    assert await provider.is_speech(pcm_samples(0, 1), 16000)
    assert len(session.calls) == 3


def test_silero_constructs_a_single_thread_onnx_session(monkeypatch):
    """Would fail if the local ONNX session ignored its intended model path or thread cap."""
    created = {}

    class FakeSessionOptions:
        intra_op_num_threads = None
        inter_op_num_threads = None

    class FakeSession:
        def __init__(self, model_path, sess_options):
            created["model_path"] = model_path
            created["options"] = sess_options

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(SessionOptions=FakeSessionOptions, InferenceSession=FakeSession),
    )

    SileroVADProvider("models/vad")

    assert created["model_path"].endswith("models\\vad\\src\\silero_vad\\data\\silero_vad.onnx")
    assert created["options"].intra_op_num_threads == 1
    assert created["options"].inter_op_num_threads == 1
