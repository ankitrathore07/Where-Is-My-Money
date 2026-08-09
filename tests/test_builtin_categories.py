import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

EXPECTED_BUILTINS = {
    ("Uncategorized", "expense"),
    ("Groceries", "expense"),
    ("Dining", "expense"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Health", "expense"),
    ("Income", "income"),
    ("Transfers", "transfer"),
}


def global_categories(database_url: str) -> set[tuple[str, str]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "select name, kind from categories where workspace_id is null order by name"
                    )
                ).tuples()
            )
    finally:
        engine.dispose()


def test_builtin_categories_migrate_reversibly(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'categories.db').as_posix()}"
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(configured, "head")
    assert global_categories(database_url) == EXPECTED_BUILTINS
    assert logging.getLogger("where_is_my_money.config").disabled is False

    command.downgrade(configured, "0005_accounts_balances")
    assert global_categories(database_url) == set()

    command.upgrade(configured, "head")
    assert global_categories(database_url) == EXPECTED_BUILTINS
