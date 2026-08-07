from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEYS = {"api_key", "token", "authorization", "password", "secret"}


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "***" if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value
