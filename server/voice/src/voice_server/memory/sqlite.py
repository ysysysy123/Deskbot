import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from voice_server.memory.models import MemoryContext, MemoryFact, MemoryMessage, SummaryBatch

_LOGGER = logging.getLogger(__name__)


class SQLiteMemoryProvider:
    def __init__(
        self, path: Path | str, *, context_limit: int = 64, max_messages_per_device: int = 5000,
        context_max_chars: int = 8000, facts_max_chars: int = 900, facts_limit: int = 20,
    ) -> None:
        self._path = Path(path)
        self._context_limit = context_limit
        if max_messages_per_device <= 0:
            raise ValueError("max_messages_per_device must be positive")
        self._max_messages_per_device = max_messages_per_device
        self._context_max_chars = context_max_chars
        self._facts_max_chars = facts_max_chars
        self._facts_limit = facts_limit

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> int:
        _require_nonblank("device_id", device_id)
        _require_nonblank("session_id", session_id)
        _require_nonblank("content", content)
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        created_at = datetime.now(timezone.utc).isoformat()
        return await self._finish_write(self._remember, device_id, session_id, role, content, created_at)

    async def recall(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        _require_nonblank("device_id", device_id)
        if recent_limit < 0:
            raise ValueError("recent_limit must not be negative")
        return await asyncio.to_thread(self._recall, device_id, recent_limit, False, query)

    async def recall_for_turn(self, device_id: str, query: str, recent_limit: int) -> MemoryContext:
        """Include pending summary messages without changing admin pagination."""
        return await asyncio.to_thread(self._recall, device_id, recent_limit, True, query)

    async def clear(self, device_id: str) -> None:
        _require_nonblank("device_id", device_id)
        await asyncio.to_thread(self._clear, device_id)

    async def load_summary_batch(self, device_id: str, threshold: int, *, limit: int | None = None) -> SummaryBatch | None:
        _require_nonblank("device_id", device_id)
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        return await asyncio.to_thread(self._load_summary_batch, device_id, threshold, limit)

    async def list_facts(self, device_id: str, *, include_inactive: bool = False) -> list[MemoryFact]:
        return await asyncio.to_thread(self._list_facts, device_id, include_inactive)

    async def save_consolidation(self, device_id: str, summary: str, through_message_id: int, updates: list[dict]) -> None:
        await self._finish_write(self._save_consolidation, device_id, summary, through_message_id, updates)

    async def apply_fact(self, device_id: str, update: dict) -> None:
        await self._finish_write(self._apply_fact, device_id, update)

    async def load_command_batch(self, device_id: str, text: str) -> SummaryBatch | None:
        return await asyncio.to_thread(self._load_command_batch, device_id, text)

    async def save_command(self, device_id: str, updates: list[dict]) -> None:
        await self._finish_write(self._save_command, device_id, updates)

    @staticmethod
    async def _finish_write(function, *args):
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # SQLite keeps running in its thread; let commit/rollback finish
            # before a clear or correction acquires the service's device lock.
            await task
            raise

    async def save_summary(self, device_id: str, summary: str, through_message_id: int) -> None:
        _require_nonblank("device_id", device_id)
        if not isinstance(summary, str):
            raise ValueError("summary must be a string")
        if not isinstance(through_message_id, int) or through_message_id < 1:
            raise ValueError("through_message_id must be positive")
        updated_at = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(
            self._save_summary, device_id, summary, through_message_id, updated_at
        )

    async def close(self) -> None:
        return None

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_messages (
                    id INTEGER PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_messages_device_id_id
                    ON memory_messages (device_id, id);
                CREATE TABLE IF NOT EXISTS memory_summaries (
                    device_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    summarized_through_message_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    fact_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    source_excerpt TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_facts_device_key ON memory_facts(device_id, fact_key);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_facts_active ON memory_facts(device_id, fact_key)
                    WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS memory_exclusions (
                    device_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (device_id, message_id)
                );
                """
            )
        finally:
            connection.close()

    def _remember(
        self, device_id: str, session_id: str, role: str, content: str, created_at: str
    ) -> int:
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO memory_messages
                        (device_id, session_id, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (device_id, session_id, role, content, created_at),
                )
                connection.execute(
                    """
                    DELETE FROM memory_messages
                    WHERE device_id = ?
                      AND id NOT IN (
                          SELECT id FROM memory_messages
                          WHERE device_id = ?
                          ORDER BY id DESC LIMIT ?
                      )
                    """,
                    (device_id, device_id, self._max_messages_per_device),
                )
                connection.execute(
                    "DELETE FROM memory_exclusions WHERE device_id = ? AND message_id NOT IN (SELECT id FROM memory_messages WHERE device_id = ?)",
                    (device_id, device_id),
                )
                return cursor.lastrowid
        finally:
            connection.close()

    def _recall(
        self, device_id: str, recent_limit: int, for_turn: bool = False, query: str = ""
    ) -> MemoryContext:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            summary_row = connection.execute(
                "SELECT summary, summarized_through_message_id FROM memory_summaries WHERE device_id = ?", (device_id,)
            ).fetchone()
            limit = recent_limit
            if for_turn:
                checkpoint = 0 if summary_row is None else summary_row[1]
                pending = connection.execute(
                    "SELECT COUNT(*) FROM memory_messages WHERE device_id = ? AND id > ? AND id NOT IN (SELECT message_id FROM memory_exclusions WHERE device_id = ?)",
                    (device_id, checkpoint, device_id),
                ).fetchone()[0]
                limit = min(max(recent_limit, pending), self._context_limit)
                if pending > self._context_limit:
                    _LOGGER.warning("Unsummarized context truncated: device=%s messages=%s limit=%s", device_id, pending, self._context_limit)
            rows = connection.execute(
                """
                SELECT id, device_id, session_id, role, content, created_at
                FROM memory_messages
                WHERE device_id = ? AND id NOT IN (SELECT message_id FROM memory_exclusions WHERE device_id = ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (device_id, device_id, limit),
            ).fetchall()
            relevant_rows = []
            if query and query.strip():
                terms = _memory_terms(query)
                if terms:
                    clauses = " OR ".join("content LIKE ?" for _ in terms)
                    score = " + ".join("CASE WHEN content LIKE ? THEN 1 ELSE 0 END" for _ in terms)
                    patterns = [f"%{term}%" for term in terms]
                    recent_ids = [row[0] for row in rows]
                    excluded = f" AND id NOT IN ({','.join('?' for _ in recent_ids)})" if recent_ids else ""
                    relevant_rows = connection.execute(
                        f"""
                        SELECT id, device_id, session_id, role, content, created_at,
                               ({score}) AS relevance
                        FROM memory_messages
                        WHERE device_id = ? AND ({clauses}) {excluded}
                          AND id NOT IN (SELECT message_id FROM memory_exclusions WHERE device_id = ?)
                        ORDER BY relevance DESC, id DESC
                        LIMIT 5
                        """,
                        (*patterns, device_id, *patterns, *recent_ids, device_id),
                    ).fetchall()
            facts = self._facts_from_connection(connection, device_id, False)
        finally:
            connection.close()
        messages = [_message_from_row(row) for row in reversed(rows)]
        relevant = [_message_from_row(row) for row in relevant_rows]
        summary = "" if summary_row is None else summary_row[0]
        if for_turn:
            # Spend the local context budget on facts and recent dialogue first.
            terms = _memory_terms(query)
            facts.sort(key=lambda fact: (sum(term in fact.content for term in terms), fact.importance, fact.id), reverse=True)
            picked_facts, used = [], 0
            for fact in facts:
                if used + len(fact.content) <= self._facts_max_chars and len(picked_facts) < self._facts_limit:
                    picked_facts.append(fact)
                    used += len(fact.content)
            facts = picked_facts
            remaining = max(0, self._context_max_chars - used)
            picked_messages = []
            for message in reversed(messages):
                if len(message.content) > remaining:
                    break
                picked_messages.append(message)
                remaining -= len(message.content)
            messages = list(reversed(picked_messages))
            summary = summary[:min(1500, remaining)]
            remaining -= len(summary)
            picked_relevant = []
            for message in relevant:
                if len(message.content) <= remaining:
                    picked_relevant.append(message)
                    remaining -= len(message.content)
            relevant = picked_relevant
        return MemoryContext(summary=summary, recent_messages=messages, relevant_memories=relevant, facts=facts)

    def _clear(self, device_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM memory_messages WHERE device_id = ?", (device_id,))
                connection.execute("DELETE FROM memory_summaries WHERE device_id = ?", (device_id,))
                connection.execute("DELETE FROM memory_facts WHERE device_id = ?", (device_id,))
                connection.execute("DELETE FROM memory_exclusions WHERE device_id = ?", (device_id,))
        finally:
            connection.close()

    def _load_summary_batch(self, device_id: str, threshold: int, limit: int | None = None) -> SummaryBatch | None:
        connection = self._connect()
        try:
            summary_row = connection.execute(
                """
                SELECT summary, summarized_through_message_id
                FROM memory_summaries
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            previous_summary = "" if summary_row is None else summary_row[0]
            checkpoint = 0 if summary_row is None else summary_row[1]
            rows = connection.execute(
                """
                SELECT id, device_id, session_id, role, content, created_at
                FROM memory_messages
                WHERE device_id = ? AND id > ? AND id NOT IN
                    (SELECT message_id FROM memory_exclusions WHERE device_id = ?)
                ORDER BY id ASC
                LIMIT ?
                """,
                (device_id, checkpoint, device_id, limit or threshold),
            ).fetchall()
        finally:
            connection.close()
        if len(rows) < threshold:
            return None
        messages = [_message_from_row(row) for row in rows]
        return SummaryBatch(
            messages=messages,
            through_message_id=messages[-1].id,
            previous_summary=previous_summary,
        )

    def _save_summary(
        self, device_id: str, summary: str, through_message_id: int, updated_at: str
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 1 FROM memory_messages WHERE id = ? AND device_id = ?",
                (through_message_id, device_id),
            ).fetchone()
            if row is None:
                raise ValueError("through_message_id does not belong to device_id")
            checkpoint_row = connection.execute(
                """
                SELECT summarized_through_message_id
                FROM memory_summaries
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if checkpoint_row is not None and through_message_id <= checkpoint_row[0]:
                raise ValueError("through_message_id must advance the current checkpoint")
            connection.execute(
                """
                INSERT INTO memory_summaries
                    (device_id, summary, summarized_through_message_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    summary = excluded.summary,
                    summarized_through_message_id = excluded.summarized_through_message_id,
                    updated_at = excluded.updated_at
                """,
                (device_id, summary, through_message_id, updated_at),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _list_facts(self, device_id: str, include_inactive: bool) -> list[MemoryFact]:
        connection = self._connect()
        try:
            return self._facts_from_connection(connection, device_id, include_inactive)
        finally:
            connection.close()

    @staticmethod
    def _facts_from_connection(connection, device_id: str, include_inactive: bool) -> list[MemoryFact]:
        status = "" if include_inactive else " AND status = 'active'"
        rows = connection.execute(
            "SELECT id, device_id, fact_key, content, category, importance, source_message_id, "
            "source_excerpt, keywords, updated_at, status FROM memory_facts WHERE device_id = ?"
            + status + " ORDER BY importance DESC, id DESC", (device_id,),
        ).fetchall()
        return [MemoryFact(*row[:8], json.loads(row[8]), *row[9:]) for row in rows]

    def _write_fact(self, connection, device_id: str, update: dict) -> list[str]:
        key, source_id = update["fact_key"], update["source_message_id"]
        source = connection.execute(
            "SELECT content FROM memory_messages WHERE device_id = ? AND id = ? AND role = 'user'",
            (device_id, source_id),
        ).fetchone()
        if source is None:
            raise ValueError("fact source must belong to this device and be a user message")
        previous = connection.execute(
            "SELECT source_message_id, keywords FROM memory_facts WHERE device_id = ? AND fact_key = ? ORDER BY id DESC",
            (device_id, key),
        ).fetchall()
        if previous and source_id <= previous[0][0]:
            return []  # Do not let an old batch restore a newer correction or deletion.
        old_keywords = [word for row in previous for word in json.loads(row[1])]
        keywords = list(dict.fromkeys([*old_keywords, *update["keywords"]]))
        replacing = bool(previous) or update["action"] == "forget"
        if replacing:
            clauses = " OR ".join("content LIKE ?" for _ in keywords)
            connection.execute(
                "INSERT OR IGNORE INTO memory_exclusions(device_id, message_id) "
                "SELECT device_id, id FROM memory_messages WHERE device_id = ? AND id < ? AND (" + clauses + ")",
                (device_id, source_id, *(f"%{word}%" for word in keywords)),
            )
        connection.execute(
            "UPDATE memory_facts SET status = 'superseded' WHERE device_id = ? AND fact_key = ? AND status = 'active'",
            (device_id, key),
        )
        connection.execute(
            "INSERT INTO memory_facts(device_id, fact_key, content, category, importance, "
            "source_message_id, source_excerpt, keywords, updated_at, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (device_id, key, update["content"], update["category"], update["importance"], source_id,
             source[0][:300], json.dumps(update["keywords"], ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(), "forgotten" if update["action"] == "forget" else "active"),
        )
        return keywords if replacing else []

    def _save_consolidation(self, device_id: str, summary: str, through_message_id: int, updates: list[dict]) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint = connection.execute(
                "SELECT summarized_through_message_id FROM memory_summaries WHERE device_id = ?", (device_id,),
            ).fetchone()
            if checkpoint and through_message_id <= checkpoint[0]:
                raise ValueError("consolidation must advance checkpoint")
            if connection.execute("SELECT 1 FROM memory_messages WHERE device_id = ? AND id = ?", (device_id, through_message_id)).fetchone() is None:
                raise ValueError("consolidation checkpoint does not belong to device")
            removed_keywords = []
            for update in sorted(updates, key=lambda item: item["source_message_id"]):
                if update["source_message_id"] > through_message_id:
                    raise ValueError("fact source is beyond consolidation checkpoint")
                removed_keywords.extend(self._write_fact(connection, device_id, update))
            # Forgotten values must also stay out of later model summaries,
            # including batches that have no new fact updates.
            forgotten = connection.execute(
                "SELECT keywords FROM memory_facts WHERE device_id = ? AND fact_key IN ("
                "SELECT fact_key FROM memory_facts WHERE status = 'forgotten' AND id IN ("
                "SELECT MAX(id) FROM memory_facts WHERE device_id = ? GROUP BY fact_key))",
                (device_id, device_id),
            ).fetchall()
            removed_keywords.extend(word for row in forgotten for word in json.loads(row[0]))
            summary = _without_topics(summary, removed_keywords)
            connection.execute(
                "INSERT INTO memory_summaries VALUES (?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
                "summary=excluded.summary, summarized_through_message_id=excluded.summarized_through_message_id, updated_at=excluded.updated_at",
                (device_id, summary, through_message_id, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _load_command_batch(self, device_id: str, text: str) -> SummaryBatch | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, device_id, session_id, role, content, created_at FROM memory_messages "
                "WHERE device_id = ? AND role = 'user' AND content = ? ORDER BY id DESC LIMIT 1",
                (device_id, text),
            ).fetchone()
            summary = connection.execute("SELECT summary FROM memory_summaries WHERE device_id = ?", (device_id,)).fetchone()
            return None if row is None else SummaryBatch([_message_from_row(row)], row[0], summary[0] if summary else "")
        finally:
            connection.close()

    def _apply_fact(self, device_id: str, update: dict) -> None:
        self._save_command(device_id, [update])

    def _save_command(self, device_id: str, updates: list[dict]) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            removed_keywords = []
            for update in sorted(updates, key=lambda item: item["source_message_id"]):
                removed_keywords.extend(self._write_fact(connection, device_id, update))
            old = connection.execute("SELECT summary FROM memory_summaries WHERE device_id = ?", (device_id,)).fetchone()
            if old and removed_keywords:
                connection.execute("UPDATE memory_summaries SET summary = ? WHERE device_id = ?",
                                   (_without_topics(old[0], removed_keywords), device_id))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _message_from_row(row: tuple[object, ...]) -> MemoryMessage:
    return MemoryMessage(
        id=row[0],
        device_id=row[1],
        session_id=row[2],
        role=row[3],
        content=row[4],
        created_at=row[5],
    )


def _require_nonblank(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _memory_terms(query: str) -> list[str]:
    normalized = re.sub(r"[\s，。！？、,.!?；;：:]+", "", query.strip())
    if not normalized:
        return []
    terms = [normalized]
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1))
    for token in re.findall(r"[A-Za-z0-9_]{2,}", normalized):
        terms.append(token)
    unique = list(dict.fromkeys(terms))
    return unique[:8]


def _without_topics(summary: str, keywords: list[str]) -> str:
    if not keywords:
        return summary
    return "".join(part for part in re.split(r"(?<=[。！？.!?\n])", summary)
                   if not any(word.lower() in part.lower() for word in keywords))
