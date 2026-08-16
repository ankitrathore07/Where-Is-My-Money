import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text


def _config(engine: Engine) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", str(engine.url))
    return configured


def migration_engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{(tmp_path / 'workspace-rules.db').as_posix()}")


def upgrade(engine: Engine, revision: str) -> None:
    command.upgrade(_config(engine), revision)


def downgrade(engine: Engine, revision: str) -> None:
    command.downgrade(_config(engine), revision)


def seed_legacy_rule(engine: Engine, *, workspace_id: int, merchant_pattern: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("insert into users (google_sub, email) values ('owner', 'owner@example.com')")
        )
        connection.execute(
            text(
                "insert into workspaces (id, name, is_personal, owner_id) "
                "values (:workspace_id, 'Personal', 1, 1)"
            ),
            {"workspace_id": workspace_id},
        )
        connection.execute(
            text(
                "insert into merchant_rules (workspace_id, merchant_pattern) "
                "values (:workspace_id, :merchant_pattern)"
            ),
            {"workspace_id": workspace_id, "merchant_pattern": merchant_pattern},
        )


def fetch_rule(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(connection.execute(text("select * from merchant_rules")).mappings().one())


def fetch_legacy_pattern(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.scalar(text("select merchant_pattern from merchant_rules"))


def test_workspace_rule_migration_converts_legacy_rule_and_round_trips(tmp_path: Path) -> None:
    engine = migration_engine(tmp_path)
    try:
        upgrade(engine, "0012_tax_refund_and_installment_tags")
        seed_legacy_rule(engine, workspace_id=1, merchant_pattern="NETFLIX COM")
        upgrade(engine, "0013_workspace_rule_engine")

        row = fetch_rule(engine)
        assert row["name"] == "NETFLIX COM"
        assert row["enabled"] == 1
        assert row["priority"] == 0
        assert json.loads(str(row["condition_json"])) == {
            "field": "merchant_key",
            "operator": "exact",
            "type": "predicate",
            "value": "NETFLIX COM",
            "version": 1,
        }

        downgrade(engine, "0012_tax_refund_and_installment_tags")
        assert fetch_legacy_pattern(engine) == "NETFLIX COM"
    finally:
        engine.dispose()
