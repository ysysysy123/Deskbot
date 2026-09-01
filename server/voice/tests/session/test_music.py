from collections.abc import AsyncIterator

from tests.fakes import FakeASR, FakeCodec, FakeLLM, FakeMemory, FakeTransport, FakeTTS, FakeVAD
from voice_server.music import MusicTrack
from voice_server.protocol.messages import ListenMessage
from voice_server.session import VoiceSession, extract_music_query


class FakeMusic:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str) -> MusicTrack:
        self.queries.append(query)
        return MusicTrack("晴天 - 周杰伦", "https://example.test/audio", {})

    async def stream_pcm(self, track: MusicTrack) -> AsyncIterator[bytes]:
        yield b"pcm-song"


def test_extract_music_query_requires_an_explicit_play_request():
    assert extract_music_query("播放周杰伦的晴天") == "周杰伦的晴天"
    assert extract_music_query("我想听晴天这首歌") == "晴天"
    assert extract_music_query("今天天气怎么样") is None


async def test_music_request_searches_and_reuses_the_device_audio_channel():
    transport = FakeTransport()
    memory = FakeMemory()
    music = FakeMusic()
    session = VoiceSession(
        device_id="device-a",
        session_id="s1",
        transport=transport,
        codec=FakeCodec(decoded=b"pcm-in", encoded={b"pcm-song": [b"opus-song"]}),
        asr=FakeASR("播放周杰伦的晴天"),
        llm=FakeLLM(),
        tts=FakeTTS(),
        memory=memory,
        vad=FakeVAD(),
        min_silence_duration_ms=120,
        recent_limit=5,
        asr_timeout_s=0.1,
        llm_timeout_s=0.1,
        tts_timeout_s=0.1,
        max_binary_bytes=64,
        max_recording_bytes=256,
        max_recording_seconds=1.0,
        error_text="播放失败",
        music=music,
    )

    await session.handle_message(ListenMessage("start", "manual", None))
    await session.handle_audio(b"input-opus")
    await session.handle_message(ListenMessage("stop", "manual", None))
    await session.wait_until_idle()

    assert music.queries == ["周杰伦的晴天"]
    assert transport.events == [
        ("json", {"session_id": "s1", "type": "stt", "text": "播放周杰伦的晴天"}),
        ("json", {"session_id": "s1", "type": "llm", "text": "晴天 - 周杰伦", "emotion": "relaxed"}),
        ("json", {"session_id": "s1", "type": "tts", "state": "start"}),
        ("json", {"session_id": "s1", "type": "tts", "state": "sentence_start", "text": "正在播放：晴天 - 周杰伦"}),
        ("bytes", b"opus-song"),
        ("json", {"session_id": "s1", "type": "tts", "state": "stop"}),
    ]
    assert memory.saved == [
        ("device-a", "s1", "user", "播放周杰伦的晴天"),
        ("device-a", "s1", "assistant", "正在播放：晴天 - 周杰伦"),
    ]
