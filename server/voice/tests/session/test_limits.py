import asyncio

import pytest

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS, FakeTransport
from voice_server.protocol.messages import ListenMessage
from voice_server.protocol.state import SessionState
from voice_server.session import SessionLimitError

from test_manual_turn import make_session


class ControlledLLM:
    def __init__(
        self,
        chunks: list[str],
        *,
        next_gates: list[asyncio.Event] | None = None,
        close_gate: asyncio.Event | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._next_gates = next_gates or []
        self._close_gate = close_gate
        self._close_error = close_error
        self._index = 0
        self.next_started = [asyncio.Event() for _ in range(max(len(chunks), len(self._next_gates), 1))]
        self.next_cancelled: list[int] = []
        self.close_started = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.closed = asyncio.Event()

    def stream(self, messages, *, max_tokens=None):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        index = self._index
        if index >= len(self.next_started):
            self.next_started.append(asyncio.Event())
        self.next_started[index].set()
        try:
            if index < len(self._next_gates):
                await self._next_gates[index].wait()
        except asyncio.CancelledError:
            self.next_cancelled.append(index)
            raise
        if index >= len(self._chunks):
            raise StopAsyncIteration
        self._index += 1
        return self._chunks[index]

    async def aclose(self):
        self.close_started.set()
        try:
            if self._close_gate is not None:
                await self._close_gate.wait()
        except asyncio.CancelledError:
            self.close_cancelled.set()
            raise
        if self._close_error is not None:
            raise self._close_error
        self.closed.set()


async def test_audio_is_ignored_before_manual_start():
    codec = FakeCodec(decoded=b"pcm")
    session = make_session(codec=codec)

    await session.handle_audio(b"input-opus")

    assert codec.inputs == []


async def test_packet_over_binary_limit_is_rejected_without_decoding():
    codec = FakeCodec(decoded=b"pcm")
    session = make_session(codec=codec, max_binary_bytes=3)
    await session.handle_message(ListenMessage("start", "manual", None))

    with pytest.raises(SessionLimitError) as error:
        await session.handle_audio(b"four")

    assert error.value.close_code == 1009
    assert codec.inputs == []


async def test_cumulative_recording_bytes_over_limit_is_rejected_before_decode():
    codec = FakeCodec(decoded=b"pcm")
    session = make_session(codec=codec, max_recording_bytes=3)
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"one")

    with pytest.raises(SessionLimitError) as error:
        await session.handle_audio(b"two")

    assert error.value.close_code == 1009
    assert codec.inputs == [b"one"]


async def test_decoded_duration_boundary_is_allowed_but_overage_is_rejected():
    codec = FakeCodec(decoded=b"x" * 32_000)
    session = make_session(codec=codec, max_recording_seconds=1.0)
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"first")

    with pytest.raises(SessionLimitError) as error:
        await session.handle_audio(b"second")

    assert error.value.close_code == 1009
    assert codec.inputs == [b"first", b"second"]


async def test_second_manual_stop_does_not_start_another_pipeline():
    gate = asyncio.Event()
    asr = FakeASR("hello", gate=gate)
    session = make_session(asr=asr, codec=FakeCodec(decoded=b"pcm"))
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await asr.started.wait()
    await session.handle_message(ListenMessage("stop", "manual", None))
    gate.set()
    await session.wait_until_idle()

    assert len(asr.inputs) == 1


@pytest.mark.parametrize("kind", ("asr", "llm", "tts"))
async def test_provider_timeouts_cancel_gated_work_and_return_idle(kind: str):
    gate = asyncio.Event()
    asr = FakeASR("hello", gate=gate) if kind == "asr" else FakeASR("hello")
    llm = FakeLLM(["reply."], gate=gate) if kind == "llm" else FakeLLM(["reply."])
    tts = FakeTTS({"reply.": [b"pcm"]}, gate=gate) if kind == "tts" else FakeTTS({"reply.": [b"pcm"]})
    memory = FakeMemory()
    transport = FakeTransport()
    session = make_session(
        asr=asr,
        llm=llm,
        tts=tts,
        memory=memory,
        transport=transport,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm": [b"opus"]}),
        asr_timeout_s=0.01,
        llm_timeout_s=0.01,
        tts_timeout_s=0.01,
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    provider = {"asr": asr, "llm": llm, "tts": tts}[kind]
    assert provider.cancelled.is_set()
    assert session.state is SessionState.IDLE
    expected_saved = [] if kind == "asr" else [("device-a", "s1", "user", "hello")]
    assert memory.saved == expected_saved
    assert not any(role == "assistant" for _, _, role, _ in memory.saved)
    if kind == "tts":
        assert transport.events[-1] == ("json", {"session_id": "s1", "type": "tts", "state": "stop"})


@pytest.mark.parametrize("failure", ("error", "timeout"))
async def test_asr_failure_speaks_error_text_without_saving_memory(failure: str):
    error_text = "please try again"
    gate = asyncio.Event()
    asr = FakeASR(error=RuntimeError("ASR failed")) if failure == "error" else FakeASR(gate=gate)
    transport = FakeTransport()
    memory = FakeMemory()
    tts = FakeTTS({error_text: [b"pcm-error"]})
    session = make_session(
        asr=asr,
        transport=transport,
        memory=memory,
        tts=tts,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-error": [b"opus-error"]}),
        asr_timeout_s=0.01,
        error_text=error_text,
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert tts.texts == [error_text]
    assert transport.events == [
        ("json", {"session_id": "s1", "type": "tts", "state": "start"}),
        ("json", {"session_id": "s1", "type": "tts", "state": "sentence_start", "text": error_text}),
        ("bytes", b"opus-error"),
        ("json", {"session_id": "s1", "type": "tts", "state": "stop"}),
    ]
    assert memory.saved == []
    assert session.state is SessionState.IDLE
    if failure == "timeout":
        assert asr.cancelled.is_set()


async def test_tts_time_does_not_consume_the_llm_timeout_budget():
    gate = asyncio.Event()
    transport = FakeTransport()
    memory = FakeMemory()
    tts = FakeTTS({"reply.": [b"pcm"]}, gate=gate)
    session = make_session(
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=tts,
        memory=memory,
        transport=transport,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm": [b"opus"]}),
        llm_timeout_s=0.01,
        tts_timeout_s=0.2,
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await tts.started.wait()
    await asyncio.sleep(0.03)
    gate.set()
    await session.wait_until_idle()

    assert not tts.cancelled.is_set()
    assert ("bytes", b"opus") in transport.events
    assert memory.saved[-1] == ("device-a", "s1", "assistant", "reply.")
    assert memory.summary_devices == ["device-a"]


@pytest.mark.parametrize("no_output", ("tts", "codec"))
async def test_zero_encoded_audio_stops_without_saving_assistant(no_output: str):
    transport = FakeTransport()
    memory = FakeMemory()
    audio = {} if no_output == "tts" else {"reply.": [b"pcm"]}
    codec = FakeCodec(decoded=b"pcm-in", encoded={} if no_output == "codec" else None)
    session = make_session(
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=FakeTTS(audio),
        memory=memory,
        transport=transport,
        codec=codec,
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert transport.events[-1] == ("json", {"session_id": "s1", "type": "tts", "state": "stop"})
    assert not any(kind == "bytes" for kind, _ in transport.events)
    assert memory.saved == [("device-a", "s1", "user", "hello")]
    assert memory.summary_devices == []
    assert session.state is SessionState.IDLE


@pytest.mark.parametrize("action", ("abort", "close"))
async def test_abort_and_close_cancel_gated_tts_without_late_audio(action: str):
    gate = asyncio.Event()
    transport = FakeTransport()
    memory = FakeMemory()
    tts = FakeTTS({"reply.": [b"pcm"]}, gate=gate)
    session = make_session(
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=tts,
        memory=memory,
        transport=transport,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm": [b"opus"]}),
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await tts.started.wait()

    await getattr(session, action)()
    gate.set()

    assert tts.cancelled.is_set()
    assert session._pipeline_task is not None and session._pipeline_task.done()
    assert not any(kind == "bytes" for kind, _ in transport.events)
    assert not any(role == "assistant" for _, _, role, _ in memory.saved)
    assert session.state is (SessionState.IDLE if action == "abort" else SessionState.CLOSED)
    if action == "abort":
        assert transport.events[-1] == ("json", {"session_id": "s1", "type": "tts", "state": "stop"})


async def test_llm_timeout_bounds_an_iterator_close_that_never_returns():
    next_gate = asyncio.Event()
    close_gate = asyncio.Event()
    llm = ControlledLLM([], next_gates=[next_gate], close_gate=close_gate)
    error_text = "try again"
    tts = FakeTTS({error_text: [b"pcm-error"]})
    session = make_session(
        asr=FakeASR("hello"),
        llm=llm,
        tts=tts,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-error": [b"opus-error"]}),
        error_text=error_text,
        llm_timeout_s=0.01,
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))

    async with asyncio.timeout(0.08):
        await session.wait_until_idle()

    assert llm.next_cancelled == [0]
    assert llm.close_cancelled.is_set()
    assert tts.texts == [error_text]
    assert session.state is SessionState.IDLE


async def test_abort_preserves_cancellation_when_iterator_close_raises():
    next_gate = asyncio.Event()
    llm = ControlledLLM([], next_gates=[next_gate], close_error=RuntimeError("close failed"))
    transport = FakeTransport()
    memory = FakeMemory()
    tts = FakeTTS({"try again": [b"pcm-error"]})
    session = make_session(
        asr=FakeASR("hello"),
        llm=llm,
        tts=tts,
        transport=transport,
        memory=memory,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-error": [b"opus-error"]}),
        error_text="try again",
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await llm.next_started[0].wait()

    async with asyncio.timeout(0.2):
        await session.abort()

    assert llm.next_cancelled == [0]
    assert llm.close_started.is_set()
    assert session._pipeline_task is not None and session._pipeline_task.cancelled()
    assert tts.texts == []
    assert not any(kind == "bytes" for kind, _ in transport.events)
    assert memory.saved == [("device-a", "s1", "user", "hello")]
    assert session.state is SessionState.IDLE


async def test_multiple_llm_waits_share_one_timeout_budget():
    gates = [asyncio.Event(), asyncio.Event()]
    llm = ControlledLLM(["part one ", "part two."], next_gates=gates)
    memory = FakeMemory()
    session = make_session(
        asr=FakeASR("hello"),
        llm=llm,
        memory=memory,
        codec=FakeCodec(decoded=b"pcm-in"),
        llm_timeout_s=0.2,
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))

    await llm.next_started[0].wait()
    await asyncio.sleep(0.12)
    gates[0].set()
    await llm.next_started[1].wait()
    await asyncio.sleep(0.12)
    gates[1].set()
    async with asyncio.timeout(0.4):
        await session.wait_until_idle()

    assert llm.next_cancelled == [1]
    assert llm.closed.is_set()
    assert not any(role == "assistant" for _, _, role, _ in memory.saved)
    assert session.state is SessionState.IDLE
