import inspect
from collections.abc import AsyncIterator
from typing import Any


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
        async for chunk in response:
            choices = getattr(chunk, "choices", ())
            if not choices:
                continue
            content = getattr(getattr(choices[0], "delta", None), "content", None)
            if content:
                yield content
