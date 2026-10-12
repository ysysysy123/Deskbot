import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from voice_server.memory.models import MemoryContext, MemoryMessage, SummaryBatch

_LOGGER = logging.getLogger(__name__)


class SQLiteMemoryProvider:
    def __init__(
        self, path: Path | str, *, context_limit: int = 64, max_messages_per_device: int = 5000
    ) -> None:
        self._path = Path(path)
        self._context_limit = context_limit
        if max_messages_per_device <= 0:
            raise ValueError("max_messages_per_device must be positive")
        self._max_messages_per_device = max_messages_per_device

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def remember(self, device_id: str, session_id: str, role: str, content: str) -> None:
        _require_nonblank("device_id", device_id)
        _require_nonblank("session_id", session_id)
        _require_nonblank("content", content)
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        created_at = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(self._remember, device_id, session_id, role, content, created_at)

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

    async def load_summary_batch(self, device_id: str, threshold: int) -> SummaryBatch | None:
        _require_nonblank("device_id", device_id)
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        return await asyncio.to_thread(self._load_summary_batch, device_id, threshold)

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
                """
            )
        finally:
            connection.close()

    def _remember(
        self, device_id: str, session_id: str, role: str, content: str, created_at: str
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
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
                    "SELECT COUNT(*) FROM memory_messages WHERE device_id = ? AND id > ?",
                    (device_id, checkpoint),
                ).fetchone()[0]
                limit = min(max(recent_limit, pending), self._context_limit)
                if pending > self._context_limit:
                    _LOGGER.warning("Unsummarized context truncated: device=%s messages=%s limit=%s", device_id, pending, self._context_limit)
            rows = connection.execute(
                """
                SELECT id, device_id, session_id, role, content, created_at
                FROM memory_messages
                WHERE device_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (device_id, limit),
            ).fetchall()
            relevant_rows = []
            if query and query.strip():
                terms = _memory_terms(query)
                if terms:
                    clauses = " OR ".join("content LIKE ?" for _ in terms)
                    relevant_rows = connection.execute(
                        f"""
                        SELECT id, device_id, session_id, role, content, created_at
                        FROM memory_messages
                        WHERE device_id = ? AND ({clauses})
                        ORDER BY id DESC
                        LIMIT 5
                        """,
                        (device_id, *(f"%{term}%" for term in terms)),
                    ).fetchall()
        finally:
            connection.close()
        messages = [_message_from_row(row) for row in reversed(rows)]
        relevant = [_message_from_row(row) for row in reversed(relevant_rows)]
        summary = "" if summary_row is None else summary_row[0]
        return MemoryContext(summary=summary, recent_messages=messages, relevant_memories=relevant)

    def _clear(self, device_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM memory_messages WHERE device_id = ?", (device_id,))
                connection.execute("DELETE FROM memory_summaries WHERE device_id = ?", (device_id,))
        finally:
            connection.close()

    def _load_summary_batch(self, device_id: str, threshold: int) -> SummaryBatch | None:
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
                WHERE device_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (device_id, checkpoint, threshold),
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
