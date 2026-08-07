import asyncio

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS, FakeTransport, FakeVAD
from voice_server.protocol.messages import ListenMessage

from test_manual_turn import make_session


FRAME_60MS = b"p" * 1_920


async def test_auto_sessions_keep_audio_vad_transport_and_memory_isolated():
    transport_a = FakeTransport()
    transport_b = FakeTransport()
    codec_a = FakeCodec(decoded=b"a" * 1_920, encoded={b"pcm-a": [b"opus-a"]})
    codec_b = FakeCodec(decoded=b"b" * 1_920, encoded={b"pcm-b": [b"opus-b"]})
    vad_a = FakeVAD([True, False, False])
    vad_b = FakeVAD([True, False, False])
    memory_a = FakeMemory()
    memory_b = FakeMemory()
    session_a = make_session(
        device_id="device-a",
        session_id="a1",
        transport=transport_a,
        codec=codec_a,
        vad=vad_a,
        asr=FakeASR("alpha"),
        llm=FakeLLM(["answer a."]),
        tts=FakeTTS({"answer a.": [b"pcm-a"]}),
        memory=memory_a,
    )
    session_b = make_session(
        device_id="device-b",
        session_id="b1",
        transport=transport_b,
        codec=codec_b,
        vad=vad_b,
        asr=FakeASR("bravo"),
        llm=FakeLLM(["answer b."]),
        tts=FakeTTS({"answer b.": [b"pcm-b"]}),
        memory=memory_b,
    )

    async def run_turn(session, prefix: bytes) -> None:
        await session.handle_message(ListenMessage("start", "auto", None))
        await session.handle_audio(prefix + b"speech")
        await session.handle_audio(prefix + b"silence-1")
        await session.handle_audio(prefix + b"silence-2")
        await session.wait_until_idle()

    await asyncio.gather(run_turn(session_a, b"a"), run_turn(session_b, b"b"))

    assert codec_a.inputs == [b"aspeech", b"asilence-1", b"asilence-2"]
    assert codec_b.inputs == [b"bspeech", b"bsilence-1", b"bsilence-2"]
    assert [pcm for pcm, _ in vad_a.inputs] == [b"a" * 1_920] * 3
    assert [pcm for pcm, _ in vad_b.inputs] == [b"b" * 1_920] * 3
    assert ("bytes", b"opus-a") in transport_a.events
    assert ("bytes", b"opus-b") not in transport_a.events
    assert ("bytes", b"opus-b") in transport_b.events
    assert ("bytes", b"opus-a") not in transport_b.events
    assert memory_a.saved == [
        ("device-a", "a1", "user", "alpha"),
        ("device-a", "a1", "assistant", "answer a."),
    ]
    assert memory_b.saved == [
        ("device-b", "b1", "user", "bravo"),
        ("device-b", "b1", "assistant", "answer b."),
    ]
