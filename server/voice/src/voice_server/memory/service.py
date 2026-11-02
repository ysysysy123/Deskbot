import asyncio
import json
import logging

from voice_server.memory.base import MemoryProvider, SummaryStore
from voice_server.memory.models import MemoryContext
from voice_server.memory.consolidation import INSTRUCTION, is_memory_command, is_forget_command, parse_consolidation, validate_fact_fields
from voice_server.providers.base import LLMProvider


_LOGGER = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        provider: MemoryProvider,
        store: SummaryStore,
        llm: LLMProvider,
        summary_threshold: int,
        summary_max_tokens: int = 768,
        idle_delay_s: float = 20.0,
        batch_limit: int = 64,
    ) -> None:
        self._provider = provider
        self._store = store
        self._llm = llm
        self._summary_threshold = summary_threshold
        self._summary_max_tokens = summary_max_tokens
        self._idle_delay_s = idle_delay_s
        self._batch_limit = batch_limit
        self._idle_handles: dict[str, asyncio.TimerHandle] = {}
        self._summary_tasks: set[asyncio.Task] = set()
        self._summary_tasks_by_device: dict[str, asyncio.Task] = {}
        self._summary_generations: dict[str, int] = {}
        self._summary_locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> int:
        return await self._provider.remember(device_id, session_id, role, content)

    def activity(self, device_id: str) -> None:
        handle = self._idle_handles.pop(device_id, None)
        if handle is not None:
            handle.cancel()
        task = self._summary_tasks_by_device.get(device_id)
        if task is not None and not task.done():
            task.cancel()

    async def process_command(self, device_id: str, text: str) -> str | None:
        if not is_memory_command(text):
            return None
        # Explicit commands get a durable result before saying they succeeded.
        updates = await self._summarize(device_id, self._summary_generations.get(device_id, 0), command_text=text)
        if updates is None:
            return "原文已经保存，但这次长期记忆整理没有完成，请稍后再试。"
        if not updates:
            return "没有找到可以更新的明确记忆，请具体说明要记住或忘掉什么。"
        if any(item["action"] == "forget" for item in updates):
            return "已忘掉这项长期记忆。"
        return "已更新长期记忆。"

    async def list_facts(self, device_id: str, *, include_inactive: bool = False):
        return await self._store.list_facts(device_id, include_inactive=include_inactive)

    async def set_fact(self, device_id: str, update: dict) -> None:
        update = validate_fact_fields(update)
        self.activity(device_id)
        task = self._summary_tasks_by_device.get(device_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        lock = self._summary_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            content = update["content"] if update["action"] == "upsert" else f"忘掉这项记忆：{update['content']}"
            message_id = await self._provider.remember(device_id, "memory-admin", "user", content)
            await self._store.apply_fact(device_id, {**update, "source_message_id": message_id, "source_excerpt": content})

    async def recall(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        return await self._provider.recall(device_id, query, recent_limit)

    async def recall_for_turn(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        return await self._provider.recall_for_turn(device_id, query, recent_limit)

    async def clear(self, device_id: str) -> None:
        self.activity(device_id)
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
        if self._closed:
            return
        handle = self._idle_handles.pop(device_id, None)
        if handle is not None:
            handle.cancel()
        if self._idle_delay_s:
            self._idle_handles[device_id] = asyncio.get_running_loop().call_later(
                self._idle_delay_s, self._start_summary, device_id
            )
        else:
            self._start_summary(device_id)

    def _start_summary(self, device_id: str) -> None:
        self._idle_handles.pop(device_id, None)
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

    async def _summarize(self, device_id: str, generation: int, *, command_text: str | None = None) -> list[dict] | None:
        try:
            lock = self._summary_locks.setdefault(device_id, asyncio.Lock())
            async with lock:
                if self._summary_generations.get(device_id, 0) != generation:
                    return None
                # A current explicit command must not process/confirm an older
                # backlog batch or advance past unrelated pending messages.
                batch = (await self._store.load_command_batch(device_id, command_text) if command_text is not None
                         else await self._store.load_summary_batch(device_id, self._summary_threshold, limit=self._batch_limit))
                if batch is None:
                    return []
                facts = await self._store.list_facts(device_id, include_inactive=True)
                latest_facts = {}
                for fact in sorted(facts, key=lambda fact: fact.id, reverse=True):
                    latest_facts.setdefault(fact.fact_key, fact)
                payload = {
                    "previous_summary": batch.previous_summary,
                    "facts": [{"fact_key": fact.fact_key, "status": fact.status} if fact.status == "forgotten"
                              else {"fact_key": fact.fact_key, "content": fact.content,
                                    "category": fact.category, "keywords": fact.keywords, "status": fact.status}
                              for fact in latest_facts.values()][:60],
                    "messages": [{"id": message.id, "role": message.role, "content": message.content}
                                 for message in batch.messages],
                }
                instruction = INSTRUCTION
                if command_text is not None:
                    current = next((message for message in reversed(batch.messages) if message.role == "user"), None)
                    if current is not None:
                        instruction += f"\n本次明确记忆命令来自 user 消息 {current.id}，执行该命令的更新应引用这个 ID。"
                        if is_forget_command(current.content):
                            instruction += "\n这是遗忘操作：只选出用户要忘记的话题 fact_key 和关键词，action 填 forget，不要再次记下旧偏好。"
                messages = [{"role": "system", "content": instruction},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
                output = "".join([chunk async for chunk in self._llm.stream(messages, max_tokens=self._summary_max_tokens)])
                summary, updates = parse_consolidation(output, batch)
                if self._summary_generations.get(device_id, 0) != generation:
                    return None
                if command_text is not None:
                    await self._store.save_command(device_id, updates)
                else:
                    await self._store.save_consolidation(device_id, summary, batch.through_message_id, updates)
                return updates
        except Exception:
            _LOGGER.exception("Memory summary failed for device %s", device_id)

    async def _close_all(self) -> None:
        for handle in self._idle_handles.values():
            handle.cancel()
        self._idle_handles.clear()
        await asyncio.gather(*tuple(self._summary_tasks), return_exceptions=True)
        await self._provider.close()

    def _remove_summary_task(self, device_id: str, task: asyncio.Task) -> None:
        self._summary_tasks.discard(task)
        if self._summary_tasks_by_device.get(device_id) is task:
            self._summary_tasks_by_device.pop(device_id, None)
