import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


class OpenAICompatibleLLMProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        client: Any | None = None,
    ) -> None:
        self._owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=api_key or "local",
                base_url=base_url,
                timeout=timeout_s,
            )
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def close(self) -> None:
        if not self._owns_client:
            return
        result = self._client.close()
        if inspect.isawaitable(result):
            await result

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens: int | None = None
    ) -> AsyncIterator[str]:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
            temperature=self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )
        try:
            async for chunk in response:
                choices = getattr(chunk, "choices", ())
                if not choices:
                    continue
                content = getattr(getattr(choices[0], "delta", None), "content", None)
                if content:
                    yield content
        finally:
            close = getattr(response, "close", None) or getattr(response, "aclose", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[str | ToolCall]:
        """Stream speech immediately, then yield complete function-call arguments."""
        options: dict[str, Any] = {}
        if tools:
            options.update(tools=tools, tool_choice="auto")
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=True,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            **options,
        )
        calls: dict[int, dict[str, str]] = {}
        try:
            async for chunk in response:
                choices = getattr(chunk, "choices", ())
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None)
                if content:
                    yield content
                for part in getattr(delta, "tool_calls", None) or ():
                    call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                    if getattr(part, "id", None):
                        call["id"] += part.id
                    function = getattr(part, "function", None)
                    if getattr(function, "name", None):
                        call["name"] += function.name
                    if getattr(function, "arguments", None):
                        call["arguments"] += function.arguments
            for index in sorted(calls):
                yield ToolCall(**calls[index])
        finally:
            close = getattr(response, "close", None) or getattr(response, "aclose", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
