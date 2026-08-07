from __future__ import annotations

import hmac
from dataclasses import dataclass

from voice_server.config import AuthConfig, ConfigError


class NoAuthAuthenticator:
    def authenticate(self, device_id: str, authorization: str | None) -> bool:
        return True


@dataclass(frozen=True)
class DeviceAllowlistAuthenticator:
    allowed_devices: frozenset[str]

    def authenticate(self, device_id: str, authorization: str | None) -> bool:
        return device_id in self.allowed_devices


@dataclass(frozen=True)
class BearerTokenAuthenticator:
    token: str

    def authenticate(self, device_id: str, authorization: str | None) -> bool:
        return _check_bearer_token(authorization, self.token)


def build_authenticator(config: AuthConfig) -> NoAuthAuthenticator | DeviceAllowlistAuthenticator | BearerTokenAuthenticator:
    if config.mode == "none":
        return NoAuthAuthenticator()
    if config.mode == "allowlist":
        if not config.allowed_devices:
            raise ConfigError("auth.allowed_devices is required for allowlist mode")
        return DeviceAllowlistAuthenticator(frozenset(config.allowed_devices))
    if config.mode == "bearer":
        if not config.token.strip():
            raise ConfigError("auth token is required for bearer mode")
        return BearerTokenAuthenticator(config.token)
    raise ConfigError("auth.mode must be none, allowlist, or bearer")


def check_admin_token(header: str | None, expected: str) -> bool:
    if not expected.strip():
        return True
    return _check_bearer_token(header, expected)


def _check_bearer_token(header: str | None, expected: str) -> bool:
    if header is None or not expected.strip():
        return False
    scheme, separator, token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return False
    try:
        return hmac.compare_digest(token, expected)
    except TypeError:
        return False
