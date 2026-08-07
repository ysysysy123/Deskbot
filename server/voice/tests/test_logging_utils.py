from voice_server.logging_utils import redact_secrets


def test_redacts_nested_secret_keys():
    value = {"llm": {"api_key": "abc"}, "Authorization": "Bearer x", "model": "qwen"}
    assert redact_secrets(value) == {
        "llm": {"api_key": "***"},
        "Authorization": "***",
        "model": "qwen",
    }


def test_redacts_case_insensitive_keys_in_lists_without_mutating_input():
    value = {"items": [{"TOKEN": "abc"}, {"password": "def"}], "secret": "ghi"}

    redacted = redact_secrets(value)

    assert redacted == {"items": [{"TOKEN": "***"}, {"password": "***"}], "secret": "***"}
    assert value == {"items": [{"TOKEN": "abc"}, {"password": "def"}], "secret": "ghi"}
