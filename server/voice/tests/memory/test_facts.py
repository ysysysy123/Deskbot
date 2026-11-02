import asyncio
import json
import sqlite3

import pytest

from voice_server.memory.consolidation import parse_consolidation
from voice_server.memory.service import MemoryService
from voice_server.memory.sqlite import SQLiteMemoryProvider
from tests.fakes import FakeLLM


def fact(source, content="咖啡偏好：喜欢拿铁", action="upsert"):
    return dict(fact_key="preference.coffee", content=content, action=action, category="preference",
                importance=2, source_message_id=source, keywords=["咖啡", "拿铁", "美式"])


async def test_fact_persists_across_sessions_and_corrections_hide_old_history(tmp_path):
    path = tmp_path / "memory.db"
    store = SQLiteMemoryProvider(path)
    await store.initialize()
    source = await store.remember("a", "s1", "user", "我喜欢拿铁咖啡。")
    await store.save_consolidation("a", "用户喜欢拿铁咖啡。正在学习 Python。", source, [fact(source)])
    replacement = await store.remember("a", "s2", "user", "咖啡现在改喝美式，不喝拿铁了。")
    await store.save_consolidation("a", "咖啡改喝美式。正在学习 Python。", replacement,
                                   [fact(replacement, "咖啡偏好：改喝美式")])
    reopened = SQLiteMemoryProvider(path)
    await reopened.initialize()
    context = await reopened.recall_for_turn("a", "咖啡偏好", 10)
    assert [f.content for f in context.facts] == ["咖啡偏好：改喝美式"]
    assert source not in {m.id for m in context.recent_messages + context.relevant_memories}
    assert "拿铁" not in context.summary
    assert "Python" in context.summary
    assert (await reopened.recall("b", "咖啡", 10)).facts == []
    assert [f.status for f in await reopened.list_facts("a", include_inactive=True)] == ["active", "superseded"]


async def test_forget_blocks_old_summary_retrieval_and_reextraction_then_clear_removes_facts(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    source = await store.remember("a", "s1", "user", "我喜欢拿铁咖啡。")
    await store.save_consolidation("a", "用户爱拿铁咖啡。", source, [fact(source)])
    forgotten = await store.remember("a", "s2", "user", "忘掉我的咖啡偏好。")
    await store.apply_fact("a", fact(forgotten, "已遗忘咖啡偏好", "forget"))
    context = await store.recall_for_turn("a", "咖啡", 10)
    assert context.facts == []
    assert context.summary == ""
    assert source not in {m.id for m in context.recent_messages + context.relevant_memories}
    batch = await store.load_summary_batch("a", 1)
    assert source not in {m.id for m in batch.messages}
    await store.apply_fact("a", fact(source))  # A delayed old update cannot revive it.
    assert await store.list_facts("a") == []
    later = await store.remember("a", "s3", "user", "今天天气晴朗。")
    await store.save_consolidation("a", "用户喜欢拿铁咖啡。今天天气晴朗。", later, [])
    context = await store.recall_for_turn("a", "天气", 10)
    assert "拿铁" not in context.summary
    assert "天气晴朗" in context.summary
    await store.clear("a")
    assert await store.list_facts("a", include_inactive=True) == []


async def test_facts_survive_raw_message_retention_and_context_has_character_budget(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db", max_messages_per_device=3,
                                 context_max_chars=80, facts_max_chars=30)
    await store.initialize()
    source = await store.remember("a", "s", "user", "我喜欢拿铁咖啡。")
    await store.save_consolidation("a", "对话摘要", source, [fact(source)])
    for i in range(5):
        await store.remember("a", "s", "user", f"第{i}条消息" + "内容" * 10)
    context = await store.recall_for_turn("a", "喜欢什么", 10)
    assert len(context.facts) == 1
    assert context.facts[0].source_excerpt == "我喜欢拿铁咖啡。"
    assert sum(len(m.content) for m in context.recent_messages + context.relevant_memories) + len(context.summary) + sum(len(f.content) for f in context.facts) <= 80


async def test_consolidation_validates_user_source_and_does_not_advance_on_bad_output(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "你好")
    assistant = await store.remember("a", "s", "assistant", "你可能喜欢拿铁")
    batch = await store.load_summary_batch("a", 2)
    with pytest.raises(ValueError, match="user message"):
        parse_consolidation(json.dumps({"summary": "", "fact_updates": [fact(assistant)]}), batch)
    llm = FakeLLM(["not json"])
    service = MemoryService(store, store, llm, 2, idle_delay_s=0)
    service.schedule_summary("a")
    await service.close()
    assert await store.load_summary_batch("a", 2) is not None
    assert await store.list_facts("a") == []


async def test_explicit_memory_command_persists_below_batch_threshold(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    source = await store.remember("a", "s", "user", "记住我喜欢拿铁咖啡。")
    llm = FakeLLM([json.dumps({"summary": "", "fact_updates": [fact(source)]}, ensure_ascii=False)])
    service = MemoryService(store, store, llm, 12, idle_delay_s=20)
    assert await service.process_command("a", "记住我喜欢拿铁咖啡。") == "已更新长期记忆。"
    assert len(await service.list_facts("a")) == 1
    assert await service.process_command("a", "今天天气如何？") is None
    assert len(llm.messages) == 1
    await service.close()


async def test_current_command_is_processed_ahead_of_backlog_without_skipping_other_messages(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    old = await store.remember("a", "s1", "user", "我喜欢拿铁咖啡。")
    pending = await store.remember("a", "s1", "user", "明天要交作业。")
    text = "忘掉我的咖啡偏好。"
    source = await store.remember("a", "s2", "user", text)
    llm = FakeLLM([json.dumps({"summary": "", "fact_updates": [fact(source, "已忘掉咖啡偏好", "forget")]})])
    service = MemoryService(store, store, llm, 2, batch_limit=2)
    assert await service.process_command("a", text) == "已忘掉这项长期记忆。"
    payload = json.loads(llm.messages[0][1]["content"])
    assert [message["id"] for message in payload["messages"]] == [source]
    batch = await store.load_summary_batch("a", 1, limit=10)
    assert pending in {message.id for message in batch.messages}
    assert old not in {message.id for message in batch.messages}
    await service.close()


async def test_forget_action_comes_from_user_request_and_category_from_fact_key(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    source = await store.remember("a", "s", "user", "忘掉我的咖啡偏好。")
    batch = await store.load_summary_batch("a", 1)
    update = {**fact(source), "category": "identity/preference", "action": "upsert"}
    _, updates = parse_consolidation(json.dumps({"summary": "", "fact_updates": [update]}), batch)
    assert updates[0]["category"] == "preference"
    assert updates[0]["action"] == "forget"


async def test_initialization_adds_fact_tables_without_losing_existing_messages_or_summary(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE memory_messages(id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, session_id TEXT NOT NULL,
                                     role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE memory_summaries(device_id TEXT PRIMARY KEY, summary TEXT NOT NULL,
                                      summarized_through_message_id INTEGER NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO memory_messages VALUES(1,'a','s','user','旧消息','2026-10-01');
        INSERT INTO memory_summaries VALUES('a','旧摘要',1,'2026-10-01');
    """)
    connection.close()
    store = SQLiteMemoryProvider(path)
    await store.initialize()
    context = await store.recall("a", "", 10)
    assert context.summary == "旧摘要"
    assert [m.content for m in context.recent_messages] == ["旧消息"]
    assert context.facts == []


async def test_idle_timer_resets_on_real_activity_and_shutdown_does_not_call_model(tmp_path):
    store = SQLiteMemoryProvider(tmp_path / "memory.db")
    await store.initialize()
    await store.remember("a", "s", "user", "你好")
    llm = FakeLLM([json.dumps({"summary": "问候", "fact_updates": []})])
    service = MemoryService(store, store, llm, 1, idle_delay_s=.02)
    service.schedule_summary("a")
    service.activity("a")
    await asyncio.sleep(.04)
    assert llm.messages == []
    service.schedule_summary("a")
    await asyncio.wait_for(llm.started.wait(), 1)
    await service.close()
    assert (await store.recall("a", "", 10)).summary == "问候"
