"""The boundary between a voice turn and an LLM or future Agent runtime."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from voice_server.providers.base import LLMProvider


DEFAULT_SYSTEM_PROMPT = (
    "你是桌面陪伴助手。默认用简洁、自然的中文口语回答，通常一到三句话。"
    "避免 Markdown 和不适合朗读的格式；不确定时直接说明。"
)


@dataclass(frozen=True)
class TurnRequest:
    device_id: str
    session_id: str
    turn_id: str
    text: str
    messages: list[dict[str, str]]


class DialogueBackend(Protocol):
    """Yield speakable text; propagate cancellation and close per-turn resources.

    Instances may serve multiple devices concurrently. Agent/tool execution
    belongs inside the backend, while audio and device messages stay in voice.
    """

    def stream(self, request: TurnRequest) -> AsyncIterator[str]: ...


class LLMDialogueBackend:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def stream(self, request: TurnRequest) -> AsyncIterator[str]:
        return self._llm.stream(request.messages)
