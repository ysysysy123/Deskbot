"""The boundary between a voice turn and an LLM or future Agent runtime."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from voice_server.providers.base import LLMProvider


DEFAULT_SYSTEM_PROMPT = (
    "你是桌面陪伴机器人Deskbot。你拥有双轮底盘和拟人肢体动作（点头、快速摇摆、歪头疑惑、摇头）。"
    "默认用简洁、自然、富有感情的中文口语回答，每次最多三句话，每句话尽量不超过六十个汉字。"
    "在活跃模式开启时，你会伴随对话大胆表现肢体动作（如认同或说明白时点头、开心时摇摆、困惑时歪头、否定时摇头）；"
    "在安静模式下，除非用户明确要求做动作，否则保持安静平稳。"
    "不要使用 Markdown、编号列表、代码块或连续长段；不确定时直接说明。"
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
