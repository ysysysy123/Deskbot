import asyncio
import logging

from voice_server.memory.base import MemoryProvider, SummaryStore
from voice_server.memory.models import MemoryContext
from voice_server.providers.base import LLMProvider


_LOGGER = logging.getLogger(__name__)
_SUMMARY_INSTRUCTION = (
    "请将已有摘要和新增对话整理成简洁、准确的中文长期记忆。"
    "保留用户偏好、重要事实、关系和待办事项；不要编造信息。"
)


class MemoryService:
    def __init__(
        self,
        provider: MemoryProvider,
        store: SummaryStore,
        llm: LLMProvider,
        summary_threshold: int,
        summary_max_tokens: int = 256,
    ) -> None:
        self._provider = provider
        self._store = store
        self._llm = llm
        self._summary_threshold = summary_threshold
        self._summary_max_tokens = summary_max_tokens
        self._summary_tasks: set[asyncio.Task[None]] = set()
        self._summary_tasks_by_device: dict[str, asyncio.Task[None]] = {}
        self._summary_generations: dict[str, int] = {}
        self._summary_locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> None:
        await self._provider.remember(device_id, session_id, role, content)

    async def recall(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        return await self._provider.recall(device_id, query, recent_limit)

    async def clear(self, device_id: str) -> None:
        lock = self._summary_locks.setdefault(device_id, asyncio.Lock())
        self._summary_generations[device_id] = self._summary_generations.get(device_id, 0) + 1
        task = self._summary_tasks_by_device.get(device_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._summary_tasks_by_device.get(device_id) is task:
            self._summary_tasks_by_device.pop(device_id, None)
        async with lock:
            await self._provider.clear(device_id)
            self._summary_generations[device_id] += 1

    def schedule_summary(self, device_id: str) -> None:
        if self._closed or device_id in self._summary_tasks_by_device:
            return
        generation = self._summary_generations.get(device_id, 0)
        task = asyncio.create_task(self._summarize(device_id, generation))
        self._summary_tasks.add(task)
        self._summary_tasks_by_device[device_id] = task
        task.add_done_callback(
            lambda completed: self._remove_summary_task(device_id, completed)
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        await asyncio.shield(self._close_task)

    async def _summarize(self, device_id: str, generation: int) -> None:
        try:
            batch = await self._store.load_summary_batch(device_id, self._summary_threshold)
            if batch is None:
                return
            messages = [
                {"role": "system", "content": _SUMMARY_INSTRUCTION},
                {"role": "system", "content": f"已有摘要：{batch.previous_summary}"},
                *[
                    {"role": message.role, "content": message.content}
                    for message in batch.messages
                ],
            ]
            summary = "".join(
                [
                    chunk
                    async for chunk in self._llm.stream(
                        messages, max_tokens=self._summary_max_tokens
                    )
                ]
            )
            if summary.strip():
                lock = self._summary_locks.setdefault(device_id, asyncio.Lock())
                async with lock:
                    if self._summary_generations.get(device_id, 0) != generation:
                        return
                    await self._store.save_summary(device_id, summary, batch.through_message_id)
        except Exception:
            _LOGGER.exception("Memory summary failed for device %s", device_id)

    async def _close_all(self) -> None:
        await asyncio.gather(*tuple(self._summary_tasks), return_exceptions=True)
        await self._provider.close()

    def _remove_summary_task(self, device_id: str, task: asyncio.Task[None]) -> None:
        self._summary_tasks.discard(task)
        if self._summary_tasks_by_device.get(device_id) is task:
            self._summary_tasks_by_device.pop(device_id, None)
