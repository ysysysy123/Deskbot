import asyncio
import queue
import threading

import pytest

from voice_server.memory.sqlite import SQLiteMemoryProvider


class _InterleavingConnection:
    def __init__(self, connection, store):
        self._connection = connection
        self._store = store
        self._clearing = False

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split()).upper()
        if normalized == "BEGIN IMMEDIATE":
            cursor = self._connection.execute(sql, parameters)
            self._store.write_transaction_started.set()
            return cursor
        if normalized.startswith("SELECT 1 FROM MEMORY_MESSAGES"):
            cursor = self._connection.execute(sql, parameters)
            self._store.ownership_checked.set()
            if not self._store.release_save.wait(5):
                raise TimeoutError("save was not released")
            return cursor
        if normalized.startswith("DELETE FROM MEMORY_MESSAGES"):
            self._clearing = True
            if self._store.write_transaction_started.is_set():
                self._store.clear_order.put("waiting")
                if not self._store.release_save.wait(5):
                    raise TimeoutError("clear was not released")
            return self._connection.execute(sql, parameters)
        return self._connection.execute(sql, parameters)

    def executescript(self, sql):
        return self._connection.executescript(sql)

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, *args):
        result = self._connection.__exit__(*args)
        if self._clearing:
            self._store.clear_order.put("finished")
        return result

    def close(self):
        self._connection.close()

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()


class _InterleavingSQLiteMemoryProvider(SQLiteMemoryProvider):
    def __init__(self, path):
        super().__init__(path)
        self.write_transaction_started = threading.Event()
        self.ownership_checked = threading.Event()
        self.release_save = threading.Event()
        self.clear_order = queue.Queue()

    def _connect(self):
        return _InterleavingConnection(super()._connect(), self)


async def test_memory_is_device_isolated_and_persistent(tmp_path):
    path = tmp_path / "memory.db"
    first = SQLiteMemoryProvider(path)
    await first.initialize()
    await first.remember("device-a", "s1", "user", "A remembers red")
    await first.remember("device-b", "s2", "user", "B remembers blue")
    await first.close()

    second = SQLiteMemoryProvider(path)
    await second.initialize()
    a = await second.recall("device-a", "red", 10)
    b = await second.recall("device-b", "blue", 10)
    assert [message.content for message in a.recent_messages] == ["A remembers red"]
    assert [message.content for message in b.recent_messages] == ["B remembers blue"]


async def test_clear_removes_only_target_device(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "one")
    await store.remember("b", "s", "user", "two")
    await store.clear("a")
    assert not (await store.recall("a", "", 10)).recent_messages
    assert (await store.recall("b", "", 10)).recent_messages[0].content == "two"


async def test_summary_batch_starts_after_checkpoint(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    for index in range(3):
        await store.remember("a", "s", "user", f"m{index}")
    batch = await store.load_summary_batch("a", threshold=3)
    assert [message.content for message in batch.messages] == ["m0", "m1", "m2"]
    await store.save_summary("a", "summary-1", batch.through_message_id)
    assert await store.load_summary_batch("a", threshold=1) is None
    assert (await store.recall("a", "", 2)).summary == "summary-1"


async def test_summary_and_clear_are_device_isolated(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "message-a")
    await store.remember("b", "s", "user", "message-b")
    a_batch = await store.load_summary_batch("a", threshold=1)
    b_batch = await store.load_summary_batch("b", threshold=1)
    await store.save_summary("a", "summary-a", a_batch.through_message_id)
    await store.save_summary("b", "summary-b", b_batch.through_message_id)
    await store.clear("a")
    assert (await store.recall("a", "", 10)).summary == ""
    assert (await store.recall("b", "", 10)).summary == "summary-b"
    assert (await store.recall("b", "", 10)).recent_messages[0].content == "message-b"


async def test_remember_rejects_invalid_fields_and_preserves_content(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    with pytest.raises(ValueError):
        await store.remember("", "session", "user", "content")
    with pytest.raises(ValueError):
        await store.remember("device", "", "user", "content")
    with pytest.raises(ValueError):
        await store.remember("device", "session", "user", "   ")
    with pytest.raises(ValueError):
        await store.remember("device", "session", "system", "content")
    await store.remember("device", "session", "user", "  preserved  ")
    assert (await store.recall("device", "", 1)).recent_messages[0].content == "  preserved  "


async def test_save_summary_rejects_checkpoint_regression(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    for index in range(3):
        await store.remember("a", "s", "user", f"m{index}")
    batch = await store.load_summary_batch("a", threshold=3)
    await store.save_summary("a", "newer", batch.through_message_id)

    with pytest.raises(ValueError):
        await store.save_summary("a", "stale", batch.messages[0].id)

    assert (await store.recall("a", "", 10)).summary == "newer"
    assert await store.load_summary_batch("a", threshold=1) is None


async def test_clear_cannot_commit_between_summary_ownership_check_and_upsert(tmp_path):
    store = _InterleavingSQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "message")
    batch = await store.load_summary_batch("a", threshold=1)

    save_task = asyncio.create_task(
        store.save_summary("a", "must be cleared", batch.through_message_id)
    )
    assert await asyncio.to_thread(store.ownership_checked.wait, 2)
    clear_task = asyncio.create_task(store.clear("a"))
    await asyncio.to_thread(store.clear_order.get, True, 2)
    store.release_save.set()
    await asyncio.gather(save_task, clear_task)

    context = await store.recall("a", "", 10)
    assert context.summary == ""
    assert context.recent_messages == []
