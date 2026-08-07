import pytest

from voice_server.auth import (
    BearerTokenAuthenticator,
    DeviceAllowlistAuthenticator,
    NoAuthAuthenticator,
    build_authenticator,
    check_admin_token,
)
from voice_server.config import AuthConfig, ConfigError


def test_no_auth_accepts_device():
    assert NoAuthAuthenticator().authenticate("aa:bb", None)


def test_allowlist_rejects_unknown_device():
    auth = DeviceAllowlistAuthenticator(frozenset({"aa:bb"}))
    assert auth.authenticate("aa:bb", None)
    assert not auth.authenticate("cc:dd", None)


def test_bearer_uses_exact_token():
    auth = BearerTokenAuthenticator("secret")
    assert auth.authenticate("aa:bb", "Bearer secret")
    assert not auth.authenticate("aa:bb", "Bearer secret-x")
    assert not auth.authenticate("aa:bb", "Bearer secret ")


def test_bearer_scheme_is_case_insensitive():
    assert BearerTokenAuthenticator("secret").authenticate("aa:bb", "bEaReR secret")


def test_bearer_rejects_non_ascii_presented_token():
    assert not BearerTokenAuthenticator("secret").authenticate("aa:bb", "Bearer s\u00e9cret")


def test_admin_token_is_independent():
    assert check_admin_token("Bearer admin-secret", "admin-secret")
    assert not check_admin_token("Bearer device-secret", "admin-secret")


def test_admin_token_rejects_non_ascii_presented_token():
    assert not check_admin_token("Bearer adm\u00edn-secret", "admin-secret")


def test_empty_admin_token_disables_admin_authentication():
    assert check_admin_token(None, "")


def test_whitespace_admin_token_disables_admin_authentication():
    assert check_admin_token(None, " \t")


def test_bearer_rejects_whitespace_only_expected_token():
    assert not BearerTokenAuthenticator("   ").authenticate("aa:bb", "Bearer    ")


@pytest.mark.parametrize(
    ("config", "expected_type"),
    (
        (AuthConfig(mode="none"), NoAuthAuthenticator),
        (AuthConfig(mode="allowlist", allowed_devices=("aa:bb",)), DeviceAllowlistAuthenticator),
        (AuthConfig(mode="bearer", token="secret"), BearerTokenAuthenticator),
    ),
)
def test_build_authenticator_selects_configured_policy(config, expected_type):
    assert isinstance(build_authenticator(config), expected_type)


@pytest.mark.parametrize(
    "config",
    (
        AuthConfig(mode="unknown"),
        AuthConfig(mode="allowlist"),
        AuthConfig(mode="bearer"),
        AuthConfig(mode="bearer", token="   "),
    ),
)
def test_build_authenticator_rejects_invalid_or_incomplete_config(config):
    with pytest.raises(ConfigError):
        build_authenticator(config)
