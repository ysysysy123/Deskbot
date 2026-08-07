from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS, FakeTransport, FakeVAD
from voice_server.memory.models import MemoryMessage
from voice_server.protocol.messages import ListenMessage
from voice_server.session import VoiceSession


def make_session(**overrides):
    defaults = {
        "device_id": "device-a",
        "session_id": "s1",
        "transport": FakeTransport(),
        "codec": FakeCodec(),
        "asr": FakeASR(),
        "llm": FakeLLM(),
        "tts": FakeTTS(),
        "memory": FakeMemory(),
        "vad": FakeVAD(),
        "min_silence_duration_ms": 120,
        "recent_limit": 5,
        "asr_timeout_s": 0.1,
        "llm_timeout_s": 0.1,
        "tts_timeout_s": 0.1,
        "max_binary_bytes": 64,
        "max_recording_bytes": 256,
        "max_recording_seconds": 1.0,
        "error_text": "抱歉，请再说一遍。",
    }
    defaults.update(overrides)
    return VoiceSession(**defaults)


async def test_manual_turn_runs_asr_memory_llm_tts_in_order():
    transport = FakeTransport()
    memory = FakeMemory(summary="likes tea", recent=[])
    session = make_session(
        transport=transport,
        asr=FakeASR("你好"),
        llm=FakeLLM(["你好。", "很高兴见到你！"]),
        tts=FakeTTS({"你好。": [b"pcm-a"], "很高兴见到你！": [b"pcm-b"]}),
        memory=memory,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-a": [b"opus-a"], b"pcm-b": [b"opus-b"]}),
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input-opus")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert transport.events == [
        ("json", {"session_id": "s1", "type": "stt", "text": "你好"}),
        ("json", {"session_id": "s1", "type": "llm", "text": "🙂", "emotion": "happy"}),
        ("json", {"session_id": "s1", "type": "tts", "state": "start"}),
        ("json", {"session_id": "s1", "type": "tts", "state": "sentence_start", "text": "你好。"}),
        ("bytes", b"opus-a"),
        ("json", {"session_id": "s1", "type": "tts", "state": "sentence_start", "text": "很高兴见到你！"}),
        ("bytes", b"opus-b"),
        ("json", {"session_id": "s1", "type": "tts", "state": "stop"}),
    ]
    assert memory.saved == [
        ("device-a", "s1", "user", "你好"),
        ("device-a", "s1", "assistant", "你好。很高兴见到你！"),
    ]
    assert memory.summary_devices == ["device-a"]


async def test_memory_prompt_removes_only_the_recalled_current_user_row():
    current = "same text"
    memory = FakeMemory(
        summary="remember preferences",
        recent=[
            MemoryMessage(1, "device-a", "old", "user", current, "t1"),
            MemoryMessage(2, "device-a", "s1", "user", current, "t2"),
        ],
    )
    llm = FakeLLM([])
    session = make_session(
        asr=FakeASR(current),
        llm=llm,
        memory=memory,
        codec=FakeCodec(decoded=b"pcm-in"),
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input-opus")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert llm.messages == [[
        {"role": "system", "content": "remember preferences"},
        {"role": "user", "content": current},
        {"role": "user", "content": current},
    ]]
