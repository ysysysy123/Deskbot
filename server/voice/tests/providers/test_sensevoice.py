import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from voice_server.providers.sensevoice import SenseVoiceASRProvider


class FakeSenseVoiceModel:
    def __init__(self, result):
        self._result = result
        self.generate_kwargs = None

    def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return self._result


class BlockingSenseVoiceModel:
    def __init__(self, *, block_only_first: bool = False) -> None:
        self._block_only_first = block_only_first
        self._lock = threading.Lock()
        self._calls = 0
        self.first_started = threading.Event()
        self.second_started = threading.Event()
        self.release_first = threading.Event()

    def generate(self, **_kwargs):
        with self._lock:
            self._calls += 1
            call = self._calls
        if call == 1:
            self.first_started.set()
            self.release_first.wait(timeout=2)
        else:
            self.second_started.set()
            if not self._block_only_first:
                self.release_first.wait(timeout=2)
        return [{"text": ""}]


async def event_loop_checkpoint():
    checkpoint = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
    await checkpoint


async def test_sensevoice_transcribes_and_removes_tags():
    """Would fail if tagged local ASR text leaked to the client."""
    model = FakeSenseVoiceModel([{"text": "<|zh|><|NEUTRAL|>  你好  "}])
    provider = SenseVoiceASRProvider(model=model, max_concurrency=1)

    assert await provider.transcribe(b"\x00\x00" * 1600, 16000) == "你好"
    assert model.generate_kwargs == {
        "input": b"\x00\x00" * 1600,
        "cache": {},
        "language": "auto",
        "use_itn": True,
        "batch_size_s": 60,
    }


async def test_sensevoice_rejects_wrong_sample_rate():
    """Would fail if the 16 kHz ASR model received incompatible audio."""
    provider = SenseVoiceASRProvider(model=FakeSenseVoiceModel([]), max_concurrency=1)

    with pytest.raises(ValueError, match="16000"):
        await provider.transcribe(b"audio", 8000)


def test_sensevoice_rejects_non_positive_concurrency():
    """Would fail if a zero-capacity ASR provider accepted requests forever."""
    with pytest.raises(ValueError, match="max_concurrency"):
        SenseVoiceASRProvider(model=FakeSenseVoiceModel([]), max_concurrency=0)


def test_sensevoice_factory_validates_concurrency_before_loading_model(monkeypatch):
    """Would fail if invalid configuration still paid the model-loading side effect."""
    auto_model_called = False

    def auto_model(**_kwargs):
        nonlocal auto_model_called
        auto_model_called = True
        return FakeSenseVoiceModel([])

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=auto_model))

    with pytest.raises(ValueError, match="max_concurrency"):
        SenseVoiceASRProvider.from_model_path("models/sensevoice", max_concurrency=0)

    assert not auto_model_called


async def test_sensevoice_enforces_model_concurrency_limit():
    """Would fail if the semaphore allowed two model calls to run at once."""
    model = BlockingSenseVoiceModel()
    provider = SenseVoiceASRProvider(model=model, max_concurrency=1)
    first = asyncio.create_task(provider.transcribe(b"first", 16000))
    second = None
    try:
        assert await asyncio.to_thread(model.first_started.wait, 1)
        second = asyncio.create_task(provider.transcribe(b"second", 16000))
        assert not await asyncio.to_thread(model.second_started.wait, 0.1)
    finally:
        model.release_first.set()
        await asyncio.gather(first, *(task for task in [second] if task is not None))

    assert model.second_started.is_set()


async def test_sensevoice_releases_semaphore_after_model_error():
    """Would fail if one model error permanently consumed an inference permit."""
    class ErrorThenSuccessModel:
        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("inference failed")
            return [{"text": "recovered"}]

    provider = SenseVoiceASRProvider(model=ErrorThenSuccessModel(), max_concurrency=1)

    with pytest.raises(RuntimeError, match="inference failed"):
        await provider.transcribe(b"first", 16000)
    assert await provider.transcribe(b"second", 16000) == "recovered"


async def test_sensevoice_cancellation_holds_permit_until_model_thread_finishes():
    """Would fail if caller cancellation released a permit while its model thread still ran."""
    model = BlockingSenseVoiceModel(block_only_first=True)
    provider = SenseVoiceASRProvider(model=model, max_concurrency=1)
    first = asyncio.create_task(provider.transcribe(b"first", 16000))
    second = None
    try:
        assert await asyncio.to_thread(model.first_started.wait, 1)
        first.cancel()
        second = asyncio.create_task(provider.transcribe(b"second", 16000))
        assert not await asyncio.to_thread(model.second_started.wait, 0.1)
    finally:
        model.release_first.set()
        outcomes = await asyncio.gather(
            first,
            *(task for task in [second] if task is not None),
            return_exceptions=True,
        )

    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert model.second_started.is_set()


async def test_sensevoice_repeated_cancellation_holds_permit_until_model_thread_finishes():
    """Would fail if a second cancellation cancelled the inference-task wrapper."""
    model = BlockingSenseVoiceModel(block_only_first=True)
    provider = SenseVoiceASRProvider(model=model, max_concurrency=1)
    first = asyncio.create_task(provider.transcribe(b"first", 16000))
    second = None
    try:
        assert await asyncio.to_thread(model.first_started.wait, 1)
        first.cancel()
        await event_loop_checkpoint()
        first.cancel()
        await event_loop_checkpoint()
        second = asyncio.create_task(provider.transcribe(b"second", 16000))
        assert not await asyncio.to_thread(model.second_started.wait, 0.1)
    finally:
        model.release_first.set()
        outcomes = await asyncio.gather(
            first,
            *(task for task in [second] if task is not None),
            return_exceptions=True,
        )

    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert model.second_started.is_set()


def test_sensevoice_factory_creates_local_model_without_updates(monkeypatch):
    """Would fail if the factory initialized FunASR with network updates or wrong VAD limits."""
    created = {}

    def auto_model(**kwargs):
        created.update(kwargs)
        return FakeSenseVoiceModel([])

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=auto_model))

    provider = SenseVoiceASRProvider.from_model_path("models/sensevoice", max_concurrency=2)

    assert isinstance(provider, SenseVoiceASRProvider)
    assert created == {
        "model": "models/sensevoice",
        "vad_kwargs": {"max_single_segment_time": 30000},
        "disable_update": True,
    }
