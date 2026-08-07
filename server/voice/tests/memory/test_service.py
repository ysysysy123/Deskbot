import asyncio

import pytest

from voice_server.memory.service import MemoryService
from voice_server.memory.sqlite import SQLiteMemoryProvider
from tests.fakes import FakeLLM


class _CloseTrackingSQLiteMemoryProvider(SQLiteMemoryProvider):
    def __init__(self, path):
        super().__init__(path)
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        await super().close()


async def test_summary_runs_at_threshold_and_is_saved_with_its_own_token_limit(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    llm = FakeLLM(["short ", "summary"])
    service = MemoryService(store, store, llm, summary_threshold=2, summary_max_tokens=37)
    await service.remember("a", "s", "user", "first")
    await service.remember("a", "s", "assistant", "second")

    service.schedule_summary("a")
    await service.close()

    assert (await store.recall("a", "", 10)).summary == "short summary"
    assert llm.max_tokens == [37]


async def test_summary_failure_keeps_raw_messages(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    service = MemoryService(store, store, FakeLLM(error=RuntimeError("offline")), 1)
    await service.remember("a", "s", "user", "kept")

    service.schedule_summary("a")
    await service.close()

    assert (await store.recall("a", "", 10)).recent_messages[0].content == "kept"


async def test_summary_uses_previous_summary_and_only_one_live_task_per_device(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "first")
    first_batch = await store.load_summary_batch("a", 1)
    await store.save_summary("a", "old summary", first_batch.through_message_id)
    await store.remember("a", "s", "assistant", "new message")
    llm = FakeLLM(["new summary"])
    service = MemoryService(store, store, llm, summary_threshold=1)

    service.schedule_summary("a")
    service.schedule_summary("a")
    await service.close()

    assert llm.messages == [
        [
            {
                "role": "system",
                "content": "请将已有摘要和新增对话整理成简洁、准确的中文长期记忆。保留用户偏好、重要事实、关系和待办事项；不要编造信息。",
            },
            {"role": "system", "content": "已有摘要：old summary"},
            {"role": "assistant", "content": "new message"},
        ]
    ]


async def test_blank_summary_does_not_advance_checkpoint(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    service = MemoryService(store, store, FakeLLM(["   "]), 1)
    await service.remember("a", "s", "user", "kept")

    service.schedule_summary("a")
    await service.close()

    assert await store.load_summary_batch("a", 1) is not None


async def test_cancelled_close_waiter_does_not_cancel_shared_shutdown(tmp_path):
    gate = asyncio.Event()
    store = _CloseTrackingSQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    llm = FakeLLM(["summary"], gate=gate)
    service = MemoryService(store, store, llm, summary_threshold=1)
    await service.remember("a", "s", "user", "message")
    service.schedule_summary("a")
    await llm.started.wait()

    first_close = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    first_close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_close

    gate.set()
    await service.close()

    assert (await store.recall("a", "", 10)).summary == "summary"
    assert store.close_calls == 1


async def test_clear_invalidates_an_inflight_summary_before_it_can_save(tmp_path):
    class GatedSummaryStore(SQLiteMemoryProvider):
        def __init__(self, path):
            super().__init__(path)
            self.save_started = asyncio.Event()
            self.save_gate = asyncio.Event()
            self.clear_finished = asyncio.Event()
            self.saved_summaries = []

        async def save_summary(self, device_id, summary, through_message_id):
            self.save_started.set()
            await self.save_gate.wait()
            self.saved_summaries.append((device_id, summary, through_message_id))

        async def clear(self, device_id):
            await super().clear(device_id)
            self.clear_finished.set()

    gate = asyncio.Event()
    store = GatedSummaryStore(tmp_path / "memory.db")
    await store.initialize()
    llm = FakeLLM(["stale summary"], gate=gate)
    service = MemoryService(store, store, llm, summary_threshold=1)
    await service.remember("a", "s", "user", "old message")

    service.schedule_summary("a")
    await llm.started.wait()
    gate.set()
    await store.save_started.wait()
    clear_task = asyncio.create_task(service.clear("a"))
    await store.clear_finished.wait()
    store.save_gate.set()
    await clear_task
    await service.close()

    assert store.saved_summaries == []
    context = await store.recall("a", "", 10)
    assert context.summary == ""
    assert context.recent_messages == []
