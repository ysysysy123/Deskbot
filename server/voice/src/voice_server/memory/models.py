from dataclasses import dataclass, field


@dataclass(frozen=True)
class MemoryFact:
    id: int
    device_id: str
    fact_key: str
    content: str
    category: str
    importance: int
    source_message_id: int
    source_excerpt: str
    keywords: list[str]
    updated_at: str
    status: str


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
    facts: list[MemoryFact] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryBatch:
    messages: list[MemoryMessage]
    through_message_id: int
    previous_summary: str
