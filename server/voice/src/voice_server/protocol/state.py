from __future__ import annotations

from enum import Enum


class InvalidTransition(ValueError):
    pass


class SessionState(str, Enum):
    CONNECTED = "connected"
    IDLE = "idle"
    LISTENING = "listening"
    RECOGNIZING = "recognizing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


_TRANSITIONS = {
    SessionState.CONNECTED: {SessionState.IDLE, SessionState.CLOSED},
    SessionState.IDLE: {SessionState.LISTENING, SessionState.CLOSED},
    SessionState.LISTENING: {SessionState.RECOGNIZING, SessionState.IDLE, SessionState.CLOSED},
    SessionState.RECOGNIZING: {SessionState.THINKING, SessionState.IDLE, SessionState.CLOSED},
    SessionState.THINKING: {SessionState.SPEAKING, SessionState.IDLE, SessionState.CLOSED},
    SessionState.SPEAKING: {SessionState.IDLE, SessionState.CLOSED},
    SessionState.CLOSED: set(),
}


class SessionStateMachine:
    def __init__(self) -> None:
        self.current = SessionState.CONNECTED

    def transition(self, target: SessionState) -> None:
        if target not in _TRANSITIONS[self.current]:
            raise InvalidTransition(f"cannot transition from {self.current.value} to {target}")
        self.current = target

    def abort(self) -> None:
        if self.current is not SessionState.CLOSED:
            self.current = SessionState.IDLE
