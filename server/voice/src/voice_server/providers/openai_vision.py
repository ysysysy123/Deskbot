"""Separate visual-model requests; text-only chat never receives image data."""

import inspect
from typing import Any


class OpenAICompatibleVisionProvider:
    def __init__(
        self, *, base_url: str, model: str, api_key: str, timeout_s: float,
        max_tokens: int = 512, client: Any | None = None,
    ) -> None:
        self._owns_client = client is None
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key or "local", base_url=base_url, timeout=timeout_s)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def describe(self, image_data: str, mime_type: str, question: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": "根据照片回答用户的问题，用简洁中文描述。看不清或无法判断时直接说明，不要猜测。"},
                {"role": "user", "content": [
                    {"type": "text", "text": question or "请描述摄像头拍到的画面。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                ]},
            ],
            max_tokens=self._max_tokens,
        )
        return response.choices[0].message.content or "视觉模型没有返回描述。"

    async def close(self) -> None:
        if self._owns_client:
            result = self._client.close()
            if inspect.isawaitable(result):
                await result
