import pytest

from voice_server.protocol.messages import (
    AbortMessage,
    HelloMessage,
    ListenMessage,
    ProtocolError,
    make_llm,
    make_server_hello,
    make_stt,
    make_tts,
    parse_client_message,
)


def test_parses_v1_hello():
    """Rejects a compatible hello if its v1 audio contract changes."""
    message = parse_client_message(
        '{"type":"hello","version":1,"transport":"websocket","audio_params":'
        '{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}'
    )
    assert message == HelloMessage(
        version=1,
        format="opus",
        sample_rate=16000,
        channels=1,
        frame_duration=60,
    )


def test_parses_v1_hello_with_optional_features():
    """Would fail if a compatible device hello with feature metadata were rejected."""
    assert parse_client_message(
        '{"type":"hello","version":1,"transport":"websocket","audio_params":'
        '{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60},'
        '"features":{"mcp":false}}'
    ) == HelloMessage(
        version=1,
        format="opus",
        sample_rate=16000,
        channels=1,
        frame_duration=60,
    )


def test_rejects_v2_hello():
    """Would fail if an unsupported protocol version were accepted."""
    with pytest.raises(ProtocolError) as error:
        parse_client_message(
            '{"type":"hello","version":2,"transport":"websocket","audio_params":'
            '{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}'
        )
    assert error.value.close_code == 1002


def test_rejects_hello_with_incompatible_audio_params():
    """Would fail if non-16 kHz mono Opus input were accepted."""
    with pytest.raises(ProtocolError, match="audio_params") as error:
        parse_client_message(
            '{"type":"hello","version":1,"transport":"websocket","audio_params":'
            '{"format":"pcm","sample_rate":16000,"channels":1,"frame_duration":60}}'
        )
    assert error.value.close_code == 1002


def test_rejects_noninteger_hello_numbers():
    """Would fail if JSON booleans or floats could masquerade as protocol integers."""
    for raw in (
        '{"type":"hello","version":1.0,"transport":"websocket","audio_params":'
        '{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}',
        '{"type":"hello","version":1,"transport":"websocket","audio_params":'
        '{"format":"opus","sample_rate":16000,"channels":true,"frame_duration":60}}',
    ):
        with pytest.raises(ProtocolError) as error:
            parse_client_message(raw)
        assert error.value.close_code == 1002


def test_rejects_malformed_or_unknown_client_messages():
    """Would fail if malformed JSON or unrecognized message types reached a session."""
    for raw in ('{', '{"type":"goodbye"}'):
        with pytest.raises(ProtocolError) as error:
            parse_client_message(raw)
        assert error.value.close_code == 1002


def test_parses_listen_and_abort():
    """Would fail if supported control messages could not reach a session."""
    assert parse_client_message('{"type":"listen","state":"start","mode":"manual"}') == ListenMessage(
        state="start", mode="manual", text=None
    )
    assert parse_client_message('{"type":"abort"}') == AbortMessage()


def test_parses_listen_with_optional_session_id():
    """Would fail if documented listen session metadata could not reach a session."""
    assert parse_client_message(
        '{"type":"listen","state":"start","mode":"manual","session_id":"session-1"}'
    ) == ListenMessage(state="start", mode="manual", text=None)


def test_parses_abort_with_optional_metadata():
    """Would fail if documented abort metadata could not interrupt a session."""
    assert parse_client_message(
        '{"type":"abort","session_id":"session-1","reason":"wake_word"}'
    ) == AbortMessage()


def test_rejects_listen_with_unsupported_state_or_mode():
    """Would fail if an unsupported listen control value were accepted."""
    for raw in (
        '{"type":"listen","state":"pause","mode":"manual"}',
        '{"type":"listen","state":"start","mode":"handsfree"}',
    ):
        with pytest.raises(ProtocolError) as error:
            parse_client_message(raw)
        assert error.value.close_code == 1002


def test_rejects_nonstring_listen_values_with_a_protocol_error():
    """Would fail if malformed control values escaped as an internal TypeError."""
    with pytest.raises(ProtocolError) as error:
        parse_client_message('{"type":"listen","state":[],"mode":"manual"}')
    assert error.value.close_code == 1002


@pytest.mark.parametrize(
    "raw",
    (
        '{"type":"abort","type":"abort"}',
        '{"type":"hello","version":1,"transport":"websocket","audio_params":'
        '{"format":"opus","format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}}',
    ),
)
def test_rejects_duplicate_json_members_at_every_level(raw: str):
    """Would fail if duplicate members could silently change a parsed protocol message."""
    with pytest.raises(ProtocolError) as error:
        parse_client_message(raw)
    assert error.value.close_code == 1002


def test_builds_server_hello():
    """Would fail if the ESP32 received non-v1 output audio settings."""
    assert make_server_hello("session-1") == {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "session_id": "session-1",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
    }


def test_builds_stt_and_tts_messages():
    """Would fail if transcript or TTS control payload fields drifted from v1."""
    assert make_stt("session-1", "hello") == {
        "session_id": "session-1",
        "type": "stt",
        "text": "hello",
    }
    assert make_tts("session-1", "start") == {"session_id": "session-1", "type": "tts", "state": "start"}
    assert make_tts("session-1", "sentence_start", "hello") == {
        "session_id": "session-1",
        "type": "tts",
        "state": "sentence_start",
        "text": "hello",
    }
    assert make_tts("session-1", "stop") == {"session_id": "session-1", "type": "tts", "state": "stop"}


def test_rejects_unsupported_tts_state():
    """Would fail if the server emitted a TTS lifecycle state ESP32 does not support."""
    with pytest.raises(ValueError, match="unsupported TTS state"):
        make_tts("session-1", "pause")


def test_builds_display_message_for_esp32():
    """Would fail if the ESP32 display/emotion payload lost a required field."""
    assert make_llm("session-1", "馃檪", "happy") == {
        "session_id": "session-1",
        "type": "llm",
        "text": "馃檪",
        "emotion": "happy",
    }
