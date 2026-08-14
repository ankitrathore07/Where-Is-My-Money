from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _config(database_url: str) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", database_url)
    return configured


def test_provider_aware_migration_round_trips_and_validates_institution_keys(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'provider-aware.db').as_posix()}"
    configured = _config(database_url)
    command.upgrade(configured, "0009_account_statement_imports")
    command.upgrade(configured, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "institution_key" in {column["name"] for column in inspector.get_columns("accounts")}
        assert "ck_accounts_institution_key" in {
            constraint["name"] for constraint in inspector.get_check_constraints("accounts")
        }
        with engine.begin() as connection:
            connection.execute(
                text("insert into users (google_sub, email) values ('owner', 'owner@example.com')")
            )
            connection.execute(
                text(
                    "insert into workspaces (name, is_personal, owner_id) values ('Personal', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "insert into accounts "
                    "(workspace_id, name, account_type, institution_key, institution, "
                    "is_liability) "
                    "values (1, 'Chase Checking', 'checking', 'chase', 'Chase', 0), "
                    "(1, 'Legacy Checking', 'checking', null, 'Legacy Bank', 0)"
                )
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "insert into accounts "
                        "(workspace_id, name, account_type, institution_key, is_liability) "
                        "values (1, 'Invalid', 'checking', 'invented', 0)"
                    )
                )
    finally:
        engine.dispose()

    command.downgrade(configured, "0009_account_statement_imports")
    engine = create_engine(database_url)
    try:
        assert "institution_key" not in {
            column["name"] for column in inspect(engine).get_columns("accounts")
        }
    finally:
        engine.dispose()

    command.upgrade(configured, "head")
