import pytest

from voice_server.providers.local_tts import LocalCommandTTSProvider, LocalTTSError


class _Input:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


class _Output:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, size=-1):
        return self.chunks.pop(0) if self.chunks else b""


class _Process:
    def __init__(self, chunks=(b"pcm",)):
        self.stdin = _Input()
        self.stdout = _Output(chunks)
        self.stderr = _Output(())
        self.returncode = None
        self.killed = False

    async def wait(self):
        self.returncode = 0 if not self.killed else -9
        return self.returncode

    def kill(self):
        self.killed = True


async def test_local_command_tts_streams_raw_pcm_from_stdout():
    process = _Process((b"ab", b"cd"))

    async def factory(*args, **kwargs):
        assert args == ("local-tts",)
        return process

    provider = LocalCommandTTSProvider("local-tts", chunk_size=2, subprocess_factory=factory)
    assert [chunk async for chunk in provider.synthesize("你好")] == [b"ab", b"cd"]
    assert process.stdin.data == "你好".encode()


async def test_local_command_tts_rejects_empty_output():
    process = _Process(())

    async def factory(*args, **kwargs):
        return process

    provider = LocalCommandTTSProvider("local-tts", subprocess_factory=factory)
    with pytest.raises(LocalTTSError, match="no audio"):
        async for _ in provider.synthesize("你好"):
            pass


def test_local_command_tts_validates_command():
    with pytest.raises(ValueError):
        LocalCommandTTSProvider(" ")
