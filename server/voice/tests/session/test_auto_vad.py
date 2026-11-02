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


async def test_auto_idle_silence_does_not_consume_recording_limit_and_keeps_onset():
    asr = FakeASR("hello")
    vad = FakeVAD([False] * 30 + [True, False, False])
    session = make_session(asr=asr, vad=vad, codec=FakeCodec(decoded=FRAME_60MS),
                           min_silence_duration_ms=120, max_recording_seconds=.6)
    await session.handle_message(ListenMessage("start", "auto", None))
    for _ in range(30):
        await session.handle_audio(b"silence")
    assert session._recording_seconds == 0
    assert len(session._pre_roll) <= 5760
    assert session._pcm == b""
    for packet in (b"speech", b"silence", b"silence"):
        await session.handle_audio(packet)
    await session.wait_until_idle()
    assert len(asr.inputs) == 1
    assert .18 <= len(asr.inputs[0][0]) / 32000 <= .6


async def test_auto_submits_at_duration_limit_instead_of_closing_connection():
    asr = FakeASR("hello")
    session = make_session(asr=asr, vad=FakeVAD(True), codec=FakeCodec(decoded=FRAME_60MS),
                           max_recording_seconds=.12)
    await session.handle_message(ListenMessage("start", "auto", None))
    await session.handle_audio(b"speech")
    await session.handle_audio(b"speech")
    await session.wait_until_idle()
    assert len(asr.inputs) == 1
    assert len(asr.inputs[0][0]) == 3840


async def test_shorter_silence_window_reduces_endpoint_wait_with_same_speech_frames():
    counts = []
    for threshold in (450, 600):
        asr = FakeASR("hello")
        session = make_session(asr=asr, vad=FakeVAD([True] + [False] * 12),
                               codec=FakeCodec(decoded=FRAME_60MS), min_silence_duration_ms=threshold,
                               max_recording_seconds=3, max_recording_bytes=1000)
        await session.handle_message(ListenMessage("start", "auto", None))
        await session.handle_audio(b"speech")
        count = 0
        while session.state.value == "listening":
            await session.handle_audio(b"silence")
            count += 1
        await session.wait_until_idle()
        counts.append(count)
    assert counts == [8, 10]  # 480 vs 600 ms on a 60 ms packet grid.


async def test_manual_does_not_call_vad_or_submit_before_stop():
    asr = FakeASR("hello")
    vad = FakeVAD(True)
    session = make_session(asr=asr, vad=vad, codec=FakeCodec(decoded=FRAME_60MS))

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"speech")

    assert asr.inputs == []
    assert vad.inputs == []


async def test_auto_turn_rearms_listening_for_the_next_utterance():
    asr = FakeASR("hello")
    vad = FakeVAD([True, False, False, True, False, False])
    session = make_session(
        asr=asr,
        vad=vad,
        min_silence_duration_ms=120,
        codec=FakeCodec(decoded=FRAME_60MS),
        llm=FakeLLM(["reply."]),
        tts=FakeTTS({"reply.": [b"pcm"]}),
    )

    await session.handle_message(ListenMessage("start", "auto", None))
    for packet in (b"speech-1", b"silence-1", b"silence-2"):
        await session.handle_audio(packet)
    await session.wait_until_idle()
    assert session.state.value == "listening"

    for packet in (b"speech-2", b"silence-3", b"silence-4"):
        await session.handle_audio(packet)
    await session.wait_until_idle()
    assert len(asr.inputs) == 2
