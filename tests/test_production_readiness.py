from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_trusted_hosts_load_from_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_HOSTS", '["money.example.com","localhost"]')

    configured = Settings(_env_file=None, app_env="test", secret_key="test-secret")

    assert configured.trusted_hosts == ("money.example.com", "localhost")


def test_production_rejects_wildcard_host_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_HOSTS", '["*"]')

    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(_env_file=None, app_env="production", secret_key="s" * 48)


def test_dockerfile_keeps_development_and_production_dependencies_separate() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM base AS dev" in dockerfile
    assert "FROM base AS prod" in dockerfile
    assert "FROM dev AS browser-tests" in dockerfile
    assert "FROM prod AS final" in dockerfile
    assert 'CMD ["fastapi", "run"' in dockerfile
    assert "COPY pyproject.toml uv.lock README.md" not in dockerfile


def test_operations_documentation_covers_required_recovery_paths() -> None:
    operations = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "## SQLite backup" in operations
    assert "## SQLite restore drill" in operations
    assert "PRAGMA integrity_check" in operations
    assert "## PostgreSQL migration guide" in operations
    assert "alembic upgrade head" in operations


@pytest.mark.parametrize(
    "document",
    ["architecture.md", "operations.md", "troubleshooting.md", "learning-path.md"],
)
def test_contributor_documentation_is_linked_from_readme(document: str) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert f"docs/{document}" in readme
