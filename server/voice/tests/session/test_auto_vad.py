from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeTTS, FakeTransport, FakeVAD
from voice_server.protocol.messages import ListenMessage

from test_manual_turn import make_session


FRAME_60MS = b"p" * 1_920


async def test_auto_submits_once_after_speech_then_enough_silence():
    asr = FakeASR("hello")
    vad = FakeVAD([True, False, False])
    session = make_session(
        asr=asr,
        vad=vad,
        min_silence_duration_ms=120,
        codec=FakeCodec(decoded=FRAME_60MS),
        llm=FakeLLM([]),
        tts=FakeTTS(),
    )

    await session.handle_message(ListenMessage("start", "auto", None))
    await session.handle_audio(b"speech")
    await session.handle_audio(b"silence-one")
    await session.handle_audio(b"silence-two")
    await session.wait_until_idle()

    assert len(asr.inputs) == 1
    assert len(vad.inputs) == 3


async def test_auto_does_not_submit_silence_before_any_speech():
    asr = FakeASR("hello")
    vad = FakeVAD(False)
    session = make_session(asr=asr, vad=vad, codec=FakeCodec(decoded=FRAME_60MS))

    await session.handle_message(ListenMessage("start", "auto", None))
    for _ in range(3):
        await session.handle_audio(b"silence")

    assert asr.inputs == []
    assert len(vad.inputs) == 3


async def test_manual_does_not_call_vad_or_submit_before_stop():
    asr = FakeASR("hello")
    vad = FakeVAD(True)
    session = make_session(asr=asr, vad=vad, codec=FakeCodec(decoded=FRAME_60MS))

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"speech")

    assert asr.inputs == []
    assert vad.inputs == []
