"""Application settings loaded from environment variables.

This module is the single source of truth for configuration. It is created in
PR 1 as a ready-to-use skeleton; later PRs wire it into the database session
(PR 2), auth (PR 3), and the Docker Compose environment.

Values are read from environment variables (or a local .env file) via
pydantic-settings. No secret is ever committed to source control.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    secret_key: str = "dev-only-ephemeral-key-do-not-use-in-production"
    database_url: str = "sqlite:///data/where-is-my-money.db"
    google_client_id: str = ""
    google_client_secret: str = ""


settings = Settings()
