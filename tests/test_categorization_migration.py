from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS

PR4_HEAD = "0006_builtin_categories"


def _config(database_url: str) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", database_url)
    return configured


def _category_rows(database_url: str) -> dict[str, tuple[int, str, str]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                row.name: (row.id, row.name_key, row.kind)
                for row in connection.execute(
                    text(
                        "select id, name, name_key, kind from categories where workspace_id is null"
                    )
                )
            }
    finally:
        engine.dispose()


def test_categorization_migration_expands_catalog_and_round_trips(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'categorization.db').as_posix()}"
    configured = _config(database_url)
    command.upgrade(configured, PR4_HEAD)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            original_ids = dict(
                connection.execute(
                    text("select name, id from categories where name in ('Dining', 'Health')")
                )
                .tuples()
                .all()
            )
    finally:
        engine.dispose()

    command.upgrade(configured, "head")

    rows = _category_rows(database_url)
    assert {(name, kind) for name, (_, _, kind) in rows.items()} == set(
        BUILTIN_CATEGORY_DEFINITIONS
    )
    assert rows["Dining & Drinks"][0] == original_ids["Dining"]
    assert rows["Health & Fitness"][0] == original_ids["Health"]
    assert all(name_key for _, name_key, _ in rows.values())

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        category_columns = {column["name"] for column in inspector.get_columns("categories")}
        transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
        rule_columns = {column["name"] for column in inspector.get_columns("merchant_rules")}
        category_indexes = {index["name"] for index in inspector.get_indexes("categories")}
        rule_constraints = {
            constraint["name"] for constraint in inspector.get_unique_constraints("merchant_rules")
        }
    finally:
        engine.dispose()

    assert "name_key" in category_columns
    assert "is_subscription" in transaction_columns
    assert {"is_subscription", "updated_at"} <= rule_columns
    assert {
        "uix_custom_category_name_key",
        "uix_builtin_category_name_key",
    } <= category_indexes
    assert "uix_workspace_merchant_pattern" in rule_constraints

    command.downgrade(configured, PR4_HEAD)
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "name_key" not in {column["name"] for column in inspector.get_columns("categories")}
        with engine.connect() as connection:
            names = set(
                connection.execute(
                    text("select name from categories where workspace_id is null")
                ).scalars()
            )
    finally:
        engine.dispose()
    assert {"Dining", "Health"} <= names
    assert "Dining & Drinks" not in names

    command.upgrade(configured, "head")
    assert set(_category_rows(database_url)) == {name for name, _ in BUILTIN_CATEGORY_DEFINITIONS}


def test_categorization_migration_rejects_duplicate_category_keys(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'duplicates.db').as_posix()}"
    configured = _config(database_url)
    command.upgrade(configured, PR4_HEAD)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into users (google_sub, email) "
                    "values ('duplicate-owner', 'duplicate@example.com')"
                )
            )
            connection.execute(
                text(
                    "insert into workspaces (name, is_personal, owner_id) "
                    "values ('Duplicates', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "insert into categories (workspace_id, name, kind) values "
                    "(1, 'Trips', 'expense'), (1, '  TRIPS  ', 'expense')"
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="duplicate category name"):
        command.upgrade(configured, "head")
