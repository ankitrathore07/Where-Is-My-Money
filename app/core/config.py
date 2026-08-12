"""Application settings loaded from environment variables.

This module is the single source of truth for configuration. It is created in
PR 1 as a ready-to-use skeleton; later PRs wire it into the database session
(PR 2), auth (PR 3), and the Docker Compose environment.

Values are read from environment variables (or a local .env file) via
pydantic-settings. No secret is ever committed to source control.
"""

import logging
import secrets
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_SECRET_SENTINEL = "dev-only-ephemeral-key-do-not-use-in-production"
logger = logging.getLogger("where_is_my_money.config")


class Settings(BaseSettings):
    """Strongly typed application configuration.

    Every field has a default that is safe for local development. Production
    deployments override these through environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_env: str = "development"
    secret_key: str | None = None
    database_url: str = "sqlite:///data/where-is-my-money.db"
    google_client_id: str = ""
    google_client_secret: str = ""
    upload_directory: Path = Path("data/uploads")
    max_csv_upload_bytes: int = 5 * 1024 * 1024
    max_payslip_upload_bytes: int = 10 * 1024 * 1024
    max_statement_upload_bytes: int = 10 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        """Return whether production-only security rules apply."""
        return self.app_env.casefold() == "production"

    @property
    def session_https_only(self) -> bool:
        """Require HTTPS when the browser sends the session cookie."""
        return self.is_production

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        """Generate a temporary local key or reject unsafe production keys."""
        missing_or_sentinel = not self.secret_key or self.secret_key == DEVELOPMENT_SECRET_SENTINEL
        if self.is_production:
            if missing_or_sentinel:
                raise ValueError("SECRET_KEY is required in production")
            if len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters in production")
        elif missing_or_sentinel:
            self.secret_key = secrets.token_urlsafe(48)
            logger.warning(
                "Generated an ephemeral SECRET_KEY for development; browser sessions "
                "will reset when the process restarts"
            )
        return self


settings = Settings()
