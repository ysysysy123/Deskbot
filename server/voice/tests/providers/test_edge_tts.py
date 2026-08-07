import pytest

from voice_server.providers.edge_tts import EdgeTTSProvider


class FakeCommunicator:
    def __init__(self, events):
        self._events = events

    async def stream(self):
        for event in self._events:
            yield event


class FakeTranscoder:
    def __init__(self, pcm):
        self._pcm = pcm
        self.inputs = []

    async def to_pcm(self, media):
        self.inputs.append(media)
        return self._pcm


async def test_edge_tts_collects_audio_converts_once_and_uses_voice_settings():
    """Would fail if metadata leaked into FFmpeg input or configured speech settings were dropped."""
    created = {}

    def factory(**kwargs):
        created.update(kwargs)
        return FakeCommunicator(
            [
                {"type": "WordBoundary", "text": "hello"},
                {"type": "audio", "data": b"mp3-a"},
                {"type": "audio", "data": b"mp3-b"},
            ]
        )

    transcoder = FakeTranscoder(b"pcm!")
    provider = EdgeTTSProvider(
        voice="zh-CN-XiaoxiaoNeural",
        rate="+10%",
        volume="-5%",
        transcoder=transcoder,
        communicate_factory=factory,
    )

    assert [part async for part in provider.synthesize("hello")] == [b"pcm!"]
    assert created == {
        "text": "hello",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+10%",
        "volume": "-5%",
    }
    assert transcoder.inputs == [b"mp3-amp3-b"]


async def test_edge_tts_rejects_empty_audio_before_transcoding():
    """Would fail if an empty TTS stream looked like a successful silent response."""
    transcoder = FakeTranscoder(b"unused")
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        transcoder=transcoder,
        communicate_factory=lambda **_kwargs: FakeCommunicator([{"type": "WordBoundary"}]),
    )

    with pytest.raises(ValueError, match="audio"):
        await anext(provider.synthesize("hello"))
    assert transcoder.inputs == []


async def test_edge_tts_rejects_empty_audio_payload_before_transcoding():
    """Would fail if empty audio events were treated as usable encoded media."""
    transcoder = FakeTranscoder(b"unused")
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        transcoder=transcoder,
        communicate_factory=lambda **_kwargs: FakeCommunicator(
            [
                {"type": "WordBoundary"},
                {"type": "audio", "data": b""},
                {"type": "audio", "data": b""},
            ]
        ),
    )

    with pytest.raises(ValueError, match="audio"):
        await anext(provider.synthesize("hello"))
    assert transcoder.inputs == []


async def test_edge_tts_rejects_empty_pcm_output():
    """Would fail if a successful transcode with no PCM silently produced no response."""
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        transcoder=FakeTranscoder(b""),
        communicate_factory=lambda **_kwargs: FakeCommunicator(
            [{"type": "audio", "data": b"mp3"}]
        ),
    )

    with pytest.raises(ValueError, match="PCM"):
        await anext(provider.synthesize("hello"))


async def test_edge_tts_rejects_odd_pcm_output():
    """Would fail if an incomplete int16 sample were yielded to the audio transport."""
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        transcoder=FakeTranscoder(b"pcm"),
        communicate_factory=lambda **_kwargs: FakeCommunicator(
            [{"type": "audio", "data": b"mp3"}]
        ),
    )

    with pytest.raises(ValueError, match="even"):
        await anext(provider.synthesize("hello"))


async def test_edge_tts_chunks_long_pcm_at_even_boundaries():
    """Would fail if transport chunks split int16 samples, including the final chunk."""
    transcoder = FakeTranscoder(b"abcdefghij")
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        chunk_size=4,
        transcoder=transcoder,
        communicate_factory=lambda **_kwargs: FakeCommunicator([{"type": "audio", "data": b"mp3"}]),
    )

    parts = [part async for part in provider.synthesize("hello")]

    assert parts == [b"abcd", b"efgh", b"ij"]
    assert all(len(part) % 2 == 0 for part in parts)


async def test_edge_tts_uses_an_injected_falsy_transcoder(monkeypatch):
    """Would fail if truthiness replaced a valid injected transcoder with FFmpeg."""
    class FalsyTranscoder(FakeTranscoder):
        def __bool__(self):
            return False

    def unexpected_default_transcoder():
        raise AssertionError("default FFmpeg transcoder was constructed")

    monkeypatch.setattr(
        "voice_server.providers.edge_tts.FFmpegTranscoder",
        unexpected_default_transcoder,
    )
    transcoder = FalsyTranscoder(b"pc")
    provider = EdgeTTSProvider(
        voice="voice",
        rate="+0%",
        volume="+0%",
        transcoder=transcoder,
        communicate_factory=lambda **_kwargs: FakeCommunicator([{"type": "audio", "data": b"mp3"}]),
    )

    assert [part async for part in provider.synthesize("hello")] == [b"pc"]
    assert transcoder.inputs == [b"mp3"]


def test_edge_tts_rejects_non_positive_or_odd_chunk_sizes():
    """Would fail if streaming output could split a 16-bit sample by configuration."""
    kwargs = {
        "voice": "voice",
        "rate": "+0%",
        "volume": "+0%",
        "transcoder": FakeTranscoder(b""),
        "communicate_factory": lambda **_kwargs: FakeCommunicator([]),
    }

    with pytest.raises(ValueError, match="positive even"):
        EdgeTTSProvider(chunk_size=0, **kwargs)
    with pytest.raises(ValueError, match="positive even"):
        EdgeTTSProvider(chunk_size=3, **kwargs)
