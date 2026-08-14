from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.tags.catalog import BUILTIN_TAG_NAMES


def _config(database_url: str) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", database_url)
    return configured


def test_tagging_migration_seeds_tags_backfills_subscription_and_round_trips(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'tags.db').as_posix()}"
    configured = _config(database_url)
    command.upgrade(configured, "0010_provider_aware_transaction_imports")
    engine = create_engine(database_url)
    try:
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
                    "insert into transactions "
                    "(workspace_id, date, description, amount_cents, is_subscription) "
                    "values (1, '2026-08-13', 'SERVICE', -1000, 1)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(configured, "head")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {"tags", "transaction_tags", "merchant_rule_tags"} <= set(
            inspector.get_table_names()
        )
        transaction_columns = {column["name"] for column in inspector.get_columns("transactions")}
        rule_columns = {column["name"] for column in inspector.get_columns("merchant_rules")}
        assert "billing_period_months" in transaction_columns
        assert "billing_period_months" in rule_columns
        with engine.connect() as connection:
            assert set(
                connection.execute(
                    text("select name from tags where workspace_id is null")
                ).scalars()
            ) == set(BUILTIN_TAG_NAMES)
            assert (
                connection.scalar(
                    text(
                        "select count(*) from transaction_tags tt "
                        "join tags t on t.id = tt.tag_id "
                        "where tt.transaction_id = 1 and t.name = 'Subscription'"
                    )
                )
                == 1
            )
    finally:
        engine.dispose()

    command.downgrade(configured, "0010_provider_aware_transaction_imports")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "tags" not in inspector.get_table_names()
        assert "billing_period_months" not in {
            column["name"] for column in inspector.get_columns("transactions")
        }
    finally:
        engine.dispose()

    command.upgrade(configured, "head")
