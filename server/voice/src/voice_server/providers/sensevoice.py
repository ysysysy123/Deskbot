import asyncio
import re
from typing import Any


class SenseVoiceASRProvider:
    def __init__(self, model: Any, *, max_concurrency: int = 1) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @classmethod
    def from_model_path(cls, path: str, *, max_concurrency: int = 1) -> "SenseVoiceASRProvider":
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")

        from funasr import AutoModel

        model = AutoModel(
            model=path,
            vad_kwargs={"max_single_segment_time": 30000},
            disable_update=True,
        )
        return cls(model, max_concurrency=max_concurrency)

    async def transcribe(self, pcm_audio: bytes, sample_rate: int) -> str:
        if sample_rate != 16000:
            raise ValueError("SenseVoice ASR requires 16000 Hz PCM audio")

        async with self._semaphore:
            inference = asyncio.create_task(
                asyncio.to_thread(
                    self._model.generate,
                    input=pcm_audio,
                    cache={},
                    language="auto",
                    use_itn=True,
                    batch_size_s=60,
                )
            )
            cancellation = None
            while True:
                try:
                    results = await asyncio.shield(inference)
                    break
                except asyncio.CancelledError as error:
                    if inference.cancelled():
                        raise
                    cancellation = cancellation or error
                except Exception:
                    if cancellation is None:
                        raise
                    break
            if cancellation is not None:
                try:
                    inference.result()
                except Exception:
                    pass
                raise cancellation
        if not results:
            return ""
        return re.sub(r"<\|.*?\|>", "", results[0]["text"]).strip()
