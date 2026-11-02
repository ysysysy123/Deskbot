import asyncio

import pytest

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTTS, FakeTransport, FakeVAD
from voice_server.memory.models import MemoryMessage
from voice_server.protocol.messages import ListenMessage
from voice_server.session import VoiceSession
from voice_server.dialogue import DEFAULT_SYSTEM_PROMPT


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


@pytest.mark.parametrize("first_clause_chars", [0, 18])
async def test_short_first_clause_can_start_tts_before_model_finishes(first_clause_chars):
    prefix, tail = "我会先把整个问题拆成几个清楚的步骤，", "再逐项说明。"

    class PausedDialogue:
        def __init__(self):
            self.waiting = asyncio.Event()
            self.resume = asyncio.Event()

        async def stream(self, request):
            yield prefix
            self.waiting.set()
            await self.resume.wait()
            yield tail

    backend = PausedDialogue()
    tts = FakeTTS({prefix: [b"pcm"], tail: [b"pcm"], prefix + tail: [b"pcm"]})
    session = make_session(dialogue_backend=backend, tts=tts, first_clause_chars=first_clause_chars,
                           codec=FakeCodec(encoded={b"pcm": [b"opus"]}), llm_timeout_s=1)
    await session.handle_text("请解释")
    await backend.waiting.wait()
    if first_clause_chars:
        await asyncio.wait_for(tts.started.wait(), .5)
        assert tts.texts[0] == prefix
    else:
        assert tts.texts == []
    backend.resume.set()
    await session.wait_until_idle()
    assert session.last_turn["status"] == "success"
    assert tts.texts == ([prefix, tail] if first_clause_chars else [prefix + tail])


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
    metrics = session.last_turn
    assert metrics["status"] == "success"
    assert 0 <= metrics["asr_ms"] <= metrics["first_text_ms"] <= metrics["first_audio_ms"] <= metrics["total_ms"]
    assert metrics["first_text_ms"] <= metrics["first_sentence_ms"] <= metrics["first_tts_request_ms"] <= metrics["first_pcm_ms"] <= metrics["first_audio_ms"]
    assert metrics["tts_requests"] == 2
    assert metrics["audio_packets"] == 2


async def test_empty_dialogue_response_falls_back_to_error_audio():
    transport = FakeTransport()
    session = make_session(
        transport=transport,
        asr=FakeASR("你好"),
        llm=FakeLLM([]),
        tts=FakeTTS({"抱歉，请再说一遍。": [b"pcm-error"]}),
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-error": [b"opus-error"]}),
        error_text="抱歉，请再说一遍。",
    )
    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input-opus")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert transport.events[-2:] == [
        ("bytes", b"opus-error"),
        ("json", {"session_id": "s1", "type": "tts", "state": "stop"}),
    ]
    assert session.last_turn["status"] == "error"


async def test_memory_command_uses_real_store_and_reconnected_dialogue_reads_fact(tmp_path):
    import json
    from voice_server.memory.service import MemoryService
    from voice_server.memory.sqlite import SQLiteMemoryProvider

    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    update = {"fact_key": "preference.coffee", "content": "咖啡偏好：喜欢拿铁", "importance": 2,
              "source_message_id": 1, "keywords": ["咖啡", "拿铁"], "action": "upsert"}
    consolidation_llm = FakeLLM([json.dumps({"summary": "", "fact_updates": [update]}, ensure_ascii=False)])
    memory = MemoryService(store, store, consolidation_llm, 12)
    reply = "已更新长期记忆。"
    session = make_session(memory=memory, tts=FakeTTS({reply: [b"pcm"]}),
                           codec=FakeCodec(encoded={b"pcm": [b"opus"]}))
    await session.handle_text("记住我的咖啡偏好：喜欢拿铁")
    await session.wait_until_idle()
    assert session.last_turn["status"] == "success"
    assert len(consolidation_llm.messages) == 1
    assert memory._idle_handles
    await session.close()
    dialogue_llm = FakeLLM(["你喜欢拿铁。"])
    next_session = make_session(session_id="reconnected", memory=memory, llm=dialogue_llm,
                                tts=FakeTTS({"你喜欢拿铁。": [b"pcm"]}), codec=FakeCodec(encoded={b"pcm": [b"opus"]}))
    await next_session.handle_text("我喜欢什么咖啡？")
    await next_session.wait_until_idle()
    assert next_session.last_turn["status"] == "success"
    assert any("咖啡偏好：喜欢拿铁" in message["content"] for message in dialogue_llm.messages[0])
    assert len(consolidation_llm.messages) == 1  # Ordinary question does no extra memory-model call.
    await next_session.close()
    await memory.close()


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
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT + "\n\n以下是此前对话的记忆摘要，供回答参考：\nremember preferences"},
        {"role": "user", "content": current},
        {"role": "user", "content": current},
    ]]
