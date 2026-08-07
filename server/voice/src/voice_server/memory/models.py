from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryMessage:
    id: int
    device_id: str
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True)
class MemoryContext:
    summary: str
    recent_messages: list[MemoryMessage]
    relevant_memories: list[MemoryMessage]


@dataclass(frozen=True)
class SummaryBatch:
    messages: list[MemoryMessage]
    through_message_id: int
    previous_summary: str
