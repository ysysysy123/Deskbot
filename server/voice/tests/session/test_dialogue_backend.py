import asyncio

import pytest

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS
from voice_server.protocol.messages import parse_client_message
from voice_server.protocol.state import SessionState
from test_manual_turn import make_session


class AgentBackend:
    def __init__(self):
        self.requests = []
        self.closed = asyncio.Event()

    async def stream(self, request):
        self.requests.append(request)
        try:
            yield "查好了。"
        finally:
            self.closed.set()


async def test_detect_text_uses_agent_with_identity_context_and_voice_output():
    backend = AgentBackend()
    asr, llm, memory = FakeASR(), FakeLLM(), FakeMemory(summary="用户喜欢茶")
    session = make_session(
        dialogue_backend=backend, system_prompt="你是小桌。", asr=asr, llm=llm, memory=memory,
        tts=FakeTTS({"查好了。": [b"pcm"]}), codec=FakeCodec(encoded={b"pcm": [b"opus"]}),
    )
    await session.handle_message(parse_client_message('{"type":"listen","state":"detect","text":"帮我查询"}'))
    await session.wait_until_idle()
    request = backend.requests[0]
    assert (request.device_id, request.session_id, request.text) == ("device-a", "s1", "帮我查询")
    assert request.turn_id.startswith("s1:")
    assert request.messages[0] == {"role": "system", "content": "你是小桌。"}
    assert "用户喜欢茶" in request.messages[1]["content"]
    assert request.messages[-1] == {"role": "user", "content": "帮我查询"}
    assert asr.inputs == [] and llm.messages == []
    assert ("bytes", b"opus") in session._transport.events
    assert memory.saved[-1][-2:] == ("assistant", "查好了。")
    assert backend.closed.is_set()


async def test_dialogue_reads_ahead_while_tts_is_pending_and_abort_closes_backend():
    gate, read_ahead, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Backend:
        async def stream(self, request):
            try:
                yield "第一句。"
                yield "第二句。"
                read_ahead.set()
                await gate.wait()
            finally:
                closed.set()

    tts = FakeTTS({"第一句。": [b"pcm"]}, gate=gate)
    session = make_session(dialogue_backend=Backend(), tts=tts, llm_timeout_s=2, tts_timeout_s=2)
    await session.handle_text("你好")
    await asyncio.wait_for(tts.started.wait(), 1)
    await asyncio.wait_for(read_ahead.wait(), 1)
    await session.abort()
    assert closed.is_set() and tts.cancelled.is_set()
    assert session.state is SessionState.IDLE
    assert not any(kind == "bytes" for kind, _ in session._transport.events)


async def test_abort_cancels_backend_blocked_on_full_sentence_queue():
    gate, produced, closed = asyncio.Event(), [], asyncio.Event()

    class Backend:
        async def stream(self, request):
            try:
                for index in range(100):
                    produced.append(index)
                    yield "一句。"
            finally:
                closed.set()

    tts = FakeTTS(gate=gate)
    session = make_session(dialogue_backend=Backend(), tts=tts, sentence_queue_size=1, tts_timeout_s=2)
    await session.handle_text("你好")
    await asyncio.wait_for(tts.started.wait(), 1)
    await session.abort()
    assert len(produced) <= 3
    assert closed.is_set()
    assert session._pipeline_task.cancelled()


async def test_failed_asr_logs_stage_and_recovers_for_next_turn(caplog):
    session = make_session(asr=FakeASR(error=RuntimeError("model unavailable")), codec=FakeCodec(decoded=b"pcm"))
    await session.handle_message(parse_client_message('{"type":"listen","state":"start","mode":"manual"}'))
    await session.handle_audio(b"input")
    await session.handle_message(parse_client_message('{"type":"listen","state":"stop"}'))
    await session.wait_until_idle()
    assert "stage=asr" in caplog.text and "model unavailable" in caplog.text
    assert session.state is SessionState.IDLE
