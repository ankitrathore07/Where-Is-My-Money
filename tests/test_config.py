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
