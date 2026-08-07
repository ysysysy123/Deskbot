import asyncio

import pytest

from tests.fakes import (
    FailingStopTransport,
    FakeASR,
    FakeCodec,
    FakeLLM,
    FakeMemory,
    FakeTTS,
    FakeTransport,
    FakeVAD,
    GatedTransport,
)
from voice_server.protocol.messages import AbortMessage, ListenMessage
from voice_server.protocol.state import SessionState

from test_manual_turn import make_session


class CancellationIgnoringLLM:
    def __init__(self, gate: asyncio.Event) -> None:
        self._gate = gate
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    def stream(self, messages, *, max_tokens=None):
        return self._stream()

    async def _stream(self):
        self.started.set()
        try:
            await self._gate.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self._gate.wait()
        yield "late reply."


class GatedAssistantMemory(FakeMemory):
    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.assistant_started = asyncio.Event()
        self.assistant_cancelled = asyncio.Event()

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> None:
        if role == "assistant":
            self.assistant_started.set()
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.assistant_cancelled.set()
                await self.gate.wait()
        await super().remember(device_id, session_id, role, content)


class SequencedASR(FakeASR):
    def __init__(self, texts: list[str]) -> None:
        super().__init__()
        self._texts = texts

    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str:
        self.inputs.append((pcm_audio, sample_rate))
        self.started.set()
        return self._texts.pop(0)


def make_spoken_reply_session(transport, *, memory=None, asr=None):
    return make_session(
        asr=asr or FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=FakeTTS({"reply.": [b"pcm"], "error.": [b"pcm-error"]}),
        memory=memory or FakeMemory(),
        transport=transport,
        codec=FakeCodec(
            decoded=b"pcm",
            encoded={b"pcm": [b"opus"], b"pcm-error": [b"opus-error"]},
        ),
        error_text="error.",
    )


async def start_spoken_reply(session) -> None:
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))


def tts_states(transport) -> list[str]:
    return [
        message["state"]
        for kind, message in transport.events
        if kind == "json" and message.get("type") == "tts"
    ]


async def test_next_manual_turn_retries_a_lingering_stop_before_listening():
    transport = FailingStopTransport(stop_failures=3)
    asr = SequencedASR(["hello", ""])
    session = make_spoken_reply_session(transport, asr=asr)
    await start_spoken_reply(session)
    await session.wait_until_idle()
    assert session.state is SessionState.IDLE
    assert session._tts_started is True
    assert transport.stop_attempts == 3
    assert "stop" not in tts_states(transport)

    await session.handle_message(ListenMessage("start", "manual", None))

    assert session.state is SessionState.LISTENING
    assert session._tts_started is False
    assert transport.stop_attempts == 4
    assert tts_states(transport)[-1] == "stop"
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()
    assert session.state is SessionState.IDLE
    assert len(asr.inputs) == 2


async def test_manual_turn_stays_idle_when_lingering_stop_retry_still_fails():
    transport = FailingStopTransport(stop_failures=4)
    asr = SequencedASR(["hello", ""])
    session = make_spoken_reply_session(transport, asr=asr)
    await start_spoken_reply(session)
    await session.wait_until_idle()
    old_pipeline = session._pipeline_task
    assert session.state is SessionState.IDLE
    assert session._tts_started is True
    assert transport.stop_attempts == 3

    with pytest.raises(RuntimeError, match="stop failed"):
        await session.handle_message(ListenMessage("start", "manual", None))

    assert session.state is SessionState.IDLE
    assert session._tts_started is True
    assert session._pipeline_task is old_pipeline
    assert len(asr.inputs) == 1


async def test_abort_stops_when_tts_start_was_recorded_but_send_is_cancelled():
    transport = GatedTransport("start", record_before_gate=True)
    memory = FakeMemory()
    session = make_spoken_reply_session(transport, memory=memory)
    await start_spoken_reply(session)
    await transport.send_started.wait()

    await session.abort()

    assert transport.send_cancelled.is_set()
    assert tts_states(transport) == ["start", "stop"]
    assert not any(kind == "bytes" for kind, _ in transport.events)
    assert memory.saved == [("device-a", "s1", "user", "hello")]


async def test_abort_replaces_a_cancelled_normal_stop_that_was_not_recorded():
    transport = GatedTransport("stop", record_before_gate=False)
    memory = FakeMemory()
    session = make_spoken_reply_session(transport, memory=memory)
    await start_spoken_reply(session)
    await transport.send_started.wait()

    await session.abort()

    assert transport.send_cancelled.is_set()
    assert tts_states(transport) == ["start", "sentence_start", "stop"]
    assert ("bytes", b"opus") in transport.events
    assert memory.saved == [("device-a", "s1", "user", "hello")]


async def test_abort_does_not_duplicate_a_recorded_stop_when_send_swallows_cancel():
    transport = GatedTransport("stop", record_before_gate=True, swallow_cancel=True)
    memory = FakeMemory()
    session = make_spoken_reply_session(transport, memory=memory)
    await start_spoken_reply(session)
    await transport.send_started.wait()

    await session.abort()

    assert transport.send_cancelled.is_set()
    assert tts_states(transport) == ["start", "sentence_start", "stop"]
    assert memory.saved == [("device-a", "s1", "user", "hello")]


async def test_abort_during_completed_assistant_save_keeps_full_reply_and_summary():
    transport = FakeTransport()
    memory = GatedAssistantMemory()
    session = make_spoken_reply_session(transport, memory=memory)
    await start_spoken_reply(session)
    await memory.assistant_started.wait()
    assert tts_states(transport) == ["start", "sentence_start", "stop"]
    assert ("bytes", b"opus") in transport.events

    abort_task = asyncio.create_task(session.abort())
    await memory.assistant_cancelled.wait()
    memory.gate.set()
    await abort_task

    assert memory.saved == [
        ("device-a", "s1", "user", "hello"),
        ("device-a", "s1", "assistant", "reply."),
    ]
    assert memory.summary_devices == ["device-a"]
    assert session.state is SessionState.IDLE


async def test_abort_cancels_gated_llm_but_preserves_user_memory():
    gate = asyncio.Event()
    llm = FakeLLM(["reply."], gate=gate)
    memory = FakeMemory()
    session = make_session(
        asr=FakeASR("hello"),
        llm=llm,
        memory=memory,
        codec=FakeCodec(decoded=b"pcm"),
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await llm.started.wait()
    await session.handle_message(AbortMessage())
    events_at_abort = list(session._transport.events)
    gate.set()

    assert session._pipeline_task is not None and session._pipeline_task.done()
    assert session.state is SessionState.IDLE
    assert session._transport.events == events_at_abort
    assert memory.saved == [("device-a", "s1", "user", "hello")]
    assert llm.cancelled.is_set()


async def test_abort_stops_started_tts_and_suppresses_late_audio():
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
        codec=FakeCodec(decoded=b"pcm", encoded={b"pcm": [b"opus"]}),
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await tts.started.wait()
    await session.abort()
    events_at_abort = list(transport.events)
    gate.set()

    assert session._pipeline_task is not None and session._pipeline_task.done()
    assert session.state is SessionState.IDLE
    assert events_at_abort[-1] == ("json", {"session_id": "s1", "type": "tts", "state": "stop"})
    assert transport.events == events_at_abort
    assert not any(kind == "bytes" for kind, _ in transport.events)
    assert memory.saved == [("device-a", "s1", "user", "hello")]
    assert tts.cancelled.is_set()


async def test_generation_guard_suppresses_an_old_llm_that_ignores_cancellation():
    gate = asyncio.Event()
    llm = CancellationIgnoringLLM(gate)
    transport = FakeTransport()
    memory = FakeMemory()
    session = make_session(
        asr=FakeASR("hello"),
        llm=llm,
        transport=transport,
        memory=memory,
        codec=FakeCodec(decoded=b"pcm"),
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await llm.started.wait()
    events_before_abort = list(transport.events)

    abort_task = asyncio.create_task(session.abort())
    await llm.cancelled.wait()
    gate.set()
    await abort_task

    assert transport.events == events_before_abort
    assert memory.saved == [("device-a", "s1", "user", "hello")]
    assert session.state is SessionState.IDLE


async def test_generation_guard_ignores_an_old_gated_vad_result():
    gate = asyncio.Event()
    vad = FakeVAD(True, gate=gate)
    session = make_session(vad=vad, codec=FakeCodec(decoded=b"pcm"))
    await session.handle_message(ListenMessage("start", "auto", None))
    old_audio = asyncio.create_task(session.handle_audio(b"old-audio"))
    await vad.started.wait()

    await session.handle_message(ListenMessage("start", "auto", None))
    gate.set()
    await old_audio

    assert session.state is SessionState.LISTENING
    assert session._heard_speech is False
    assert session._pcm == b""


async def test_auto_start_interrupts_speaking_and_opens_a_fresh_listening_turn():
    gate = asyncio.Event()
    tts = FakeTTS({"reply.": [b"pcm"]}, gate=gate)
    session = make_session(
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=tts,
        codec=FakeCodec(decoded=b"pcm", encoded={b"pcm": [b"opus"]}),
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await tts.started.wait()

    await session.handle_message(ListenMessage("start", "auto", None))

    assert session.state is SessionState.LISTENING
    assert session._listen_mode == "auto"


async def test_manual_start_while_speaking_is_ignored():
    gate = asyncio.Event()
    tts = FakeTTS({"reply.": [b"pcm"]}, gate=gate)
    session = make_session(
        asr=FakeASR("hello"),
        llm=FakeLLM(["reply."]),
        tts=tts,
        codec=FakeCodec(decoded=b"pcm", encoded={b"pcm": [b"opus"]}),
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await tts.started.wait()

    await session.handle_message(ListenMessage("start", "manual", None))

    assert session.state is SessionState.SPEAKING
    await session.abort()
