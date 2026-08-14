import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_generates_secret_when_missing() -> None:
    configured = Settings(_env_file=None, app_env="development", secret_key=None)

    assert len(configured.secret_key) >= 32
    assert configured.secret_key != "dev-only-ephemeral-key-do-not-use-in-production"
    assert configured.session_https_only is False


def test_development_warns_when_generating_secret(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        Settings(_env_file=None, app_env="development", secret_key=None)

    assert "ephemeral SECRET_KEY" in caplog.text


def test_production_requires_explicit_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, app_env="production", secret_key=None)


def test_production_rejects_documented_development_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            secret_key="dev-only-ephemeral-key-do-not-use-in-production",
        )


def test_production_rejects_short_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(_env_file=None, app_env="production", secret_key="too-short")


def test_production_enables_secure_cookies() -> None:
    configured = Settings(_env_file=None, app_env="production", secret_key="s" * 48)

    assert configured.is_production is True
    assert configured.session_https_only is True


def test_statement_upload_limit_is_independent_from_payslip_limit() -> None:
    configured = Settings(
        _env_file=None,
        app_env="development",
        secret_key="test-secret",
        max_payslip_upload_bytes=123,
    )

    assert configured.max_statement_upload_bytes == 10 * 1024 * 1024


def test_ai_categorization_defaults_off_without_a_key() -> None:
    configured = Settings(_env_file=None, app_env="test", secret_key="test-secret")

    assert configured.openai_api_key == ""
    assert configured.openai_categorization_enabled is False
    assert configured.openai_categorization_model == "gpt-5.4-nano"
    assert configured.openai_categorization_timeout_seconds == 8.0


@pytest.mark.parametrize("timeout", [0.9, 30.1])
def test_ai_categorization_timeout_is_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="test",
            secret_key="test-secret",
            openai_categorization_timeout_seconds=timeout,
        )


def test_environment_name_is_restricted_to_known_modes() -> None:
    with pytest.raises(ValidationError, match="development.*test.*production"):
        Settings(_env_file=None, app_env="prod", secret_key="test-secret")


@pytest.mark.parametrize("trusted_hosts", [(), ("*",), ("",)])
def test_trusted_hosts_must_be_explicit(trusted_hosts: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(
            _env_file=None,
            app_env="development",
            secret_key="test-secret",
            trusted_hosts=trusted_hosts,
        )
