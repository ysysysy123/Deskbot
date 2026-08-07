import pytest

from voice_server.protocol.state import InvalidTransition, SessionState, SessionStateMachine


def test_starts_connected():
    """Would fail if a session accepted traffic before completing the hello transition."""
    assert SessionStateMachine().current is SessionState.CONNECTED


def test_valid_voice_turn_transitions():
    """Would fail if a normal voice turn could not advance through its lifecycle."""
    machine = SessionStateMachine()
    for state in (
        SessionState.IDLE,
        SessionState.LISTENING,
        SessionState.RECOGNIZING,
        SessionState.THINKING,
        SessionState.SPEAKING,
        SessionState.IDLE,
    ):
        machine.transition(state)
    assert machine.current is SessionState.IDLE


def test_rejects_speaking_before_recognition():
    """Would fail if a session could emit audio without a completed recognition turn."""
    machine = SessionStateMachine()
    machine.transition(SessionState.IDLE)
    with pytest.raises(InvalidTransition):
        machine.transition(SessionState.SPEAKING)


@pytest.mark.parametrize(
    "state",
    (
        SessionState.CONNECTED,
        SessionState.IDLE,
        SessionState.LISTENING,
        SessionState.RECOGNIZING,
        SessionState.THINKING,
        SessionState.SPEAKING,
    ),
)
def test_abort_returns_nonclosed_state_to_idle(state: SessionState):
    """Would fail if abort left any live session unable to begin the next turn."""
    machine = SessionStateMachine()
    if state is not SessionState.CONNECTED:
        for target in (
            SessionState.IDLE,
            SessionState.LISTENING,
            SessionState.RECOGNIZING,
            SessionState.THINKING,
            SessionState.SPEAKING,
        ):
            machine.transition(target)
            if target is state:
                break
    machine.abort()
    assert machine.current is SessionState.IDLE


def test_abort_returns_activity_to_idle():
    """Would fail if abort did not clear an active listening turn."""
    machine = SessionStateMachine()
    machine.transition(SessionState.IDLE)
    machine.transition(SessionState.LISTENING)
    machine.abort()
    assert machine.current is SessionState.IDLE


def test_disconnect_transitions_from_connected_to_closed():
    """Would fail if a transport disconnect left a new session reusable."""
    machine = SessionStateMachine()
    machine.transition(SessionState.CLOSED)
    assert machine.current is SessionState.CLOSED


def test_closed_session_rejects_further_transitions():
    """Would fail if a disconnected session could be restarted."""
    machine = SessionStateMachine()
    machine.transition(SessionState.CLOSED)
    with pytest.raises(InvalidTransition):
        machine.transition(SessionState.IDLE)
