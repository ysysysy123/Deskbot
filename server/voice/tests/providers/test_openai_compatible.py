import asyncio
import sys
from types import SimpleNamespace

import pytest

from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        async def stream():
            for chunk in self._chunks:
                yield chunk

        return stream()


class FakeClient:
    def __init__(self, chunks):
        self.completions = FakeCompletions(chunks)
        self.chat = SimpleNamespace(completions=self.completions)


def chunk(content=None):
    delta = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


async def test_openai_provider_streams_nonempty_delta_content_with_configured_defaults():
    """Would fail if the OpenAI adapter lost configured generation settings or emitted empty deltas."""
    client = FakeClient([chunk("hello"), chunk(None), chunk(""), chunk(" world")])
    provider = OpenAICompatibleLLMProvider(
        base_url="http://local/v1",
        model="test-model",
        api_key="configured-key",
        temperature=0.25,
        max_tokens=123,
        timeout_s=7.5,
        client=client,
    )
    messages = [{"role": "user", "content": "say hello"}]

    assert [part async for part in provider.stream(messages)] == ["hello", " world"]
    assert client.completions.calls == [
        {
            "model": "test-model",
            "messages": messages,
            "stream": True,
            "temperature": 0.25,
            "max_tokens": 123,
        }
    ]


async def test_openai_provider_allows_a_per_call_token_override():
    """Would fail if summary requests could not use their smaller token budget."""
    client = FakeClient([chunk("summary")])
    provider = OpenAICompatibleLLMProvider(
        base_url="http://local/v1",
        model="test-model",
        api_key="key",
        temperature=0.7,
        max_tokens=1024,
        timeout_s=60,
        client=client,
    )

    assert [part async for part in provider.stream([], max_tokens=64)] == ["summary"]
    assert client.completions.calls[0]["max_tokens"] == 64


def test_openai_provider_lazily_constructs_default_client_with_local_fallback_key(monkeypatch):
    """Would fail if local OpenAI-compatible servers received a blank key or eager import was required."""
    created = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    OpenAICompatibleLLMProvider(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen",
        api_key="",
        temperature=0.7,
        max_tokens=1024,
        timeout_s=60,
    )

    assert created == {
        "api_key": "local",
        "base_url": "http://127.0.0.1:11434/v1",
        "timeout": 60,
    }


async def test_openai_provider_closes_the_client_it_constructs(monkeypatch):
    closed = asyncio.Event()

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            pass

        async def close(self):
            closed.set()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    provider = OpenAICompatibleLLMProvider(
        base_url="http://local/v1",
        model="test-model",
        api_key="key",
        temperature=0.7,
        max_tokens=1024,
        timeout_s=60,
    )

    await provider.close()

    assert closed.is_set()


async def test_openai_provider_propagates_cancellation_from_client_creation():
    """Would fail if cancellation were converted into an ordinary streaming failure."""
    class CancelledCompletions:
        async def create(self, **_kwargs):
            raise asyncio.CancelledError()

    client = SimpleNamespace(chat=SimpleNamespace(completions=CancelledCompletions()))
    provider = OpenAICompatibleLLMProvider(
        base_url="http://local/v1",
        model="test-model",
        api_key="key",
        temperature=0.7,
        max_tokens=1024,
        timeout_s=60,
        client=client,
    )

    with pytest.raises(asyncio.CancelledError):
        await anext(provider.stream([]))
