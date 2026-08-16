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


def test_workspace_rule_migration_keeps_long_legacy_pattern_in_condition(tmp_path: Path) -> None:
    engine = migration_engine(tmp_path)
    long_pattern = "A" * 255
    try:
        upgrade(engine, "0012_tax_refund_and_installment_tags")
        seed_legacy_rule(engine, workspace_id=1, merchant_pattern=long_pattern)

        upgrade(engine, "0013_workspace_rule_engine")

        row = fetch_rule(engine)
        assert row["name"] == long_pattern[:120]
        assert json.loads(str(row["condition_json"]))["value"] == long_pattern
    finally:
        engine.dispose()


def test_workspace_rule_migration_preserves_actions_and_workspace_priority(tmp_path: Path) -> None:
    engine = migration_engine(tmp_path)
    try:
        upgrade(engine, "0012_tax_refund_and_installment_tags")
        with engine.begin() as connection:
            connection.execute(
                text("insert into users (google_sub, email) values ('owner', 'owner@example.com')")
            )
            connection.execute(
                text(
                    "insert into workspaces (id, name, is_personal, owner_id) values "
                    "(1, 'First', 1, 1), (2, 'Second', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "insert into categories (workspace_id, name, name_key, kind) "
                    "values (1, 'Streaming', 'streaming', 'expense')"
                )
            )
            connection.execute(
                text(
                    "insert into tags (workspace_id, name, name_key) "
                    "values (1, 'Recurring', 'recurring')"
                )
            )
            connection.execute(
                text(
                    "insert into merchant_rules "
                    "(id, workspace_id, merchant_pattern, normalized_merchant, category_id, "
                    "is_subscription, billing_period_months, created_at) values "
                    "(1, 1, 'LATER', 'Later merchant', 1, 1, 12, '2026-08-02'), "
                    "(2, 1, 'EARLIER', null, null, 0, null, '2026-08-01'), "
                    "(3, 2, 'OTHER', null, null, 0, null, '2026-08-01')"
                )
            )
            connection.execute(
                text(
                    "insert into merchant_rule_tags (merchant_rule_id, tag_id) "
                    "select 1, id from tags where workspace_id = 1 and name = 'Recurring'"
                )
            )

        upgrade(engine, "0013_workspace_rule_engine")

        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "select id, priority, normalized_merchant, category_id, is_subscription, "
                        "billing_period_months from merchant_rules order by id"
                    )
                )
                .mappings()
                .all()
            )
            assert [(row["id"], row["priority"]) for row in rows] == [(1, 1), (2, 0), (3, 0)]
            assert dict(rows[0]) == {
                "id": 1,
                "priority": 1,
                "normalized_merchant": "Later merchant",
                "category_id": 1,
                "is_subscription": 1,
                "billing_period_months": 12,
            }
            assert (
                connection.scalar(
                    text(
                        "select t.name from merchant_rule_tags mrt "
                        "join tags t on t.id = mrt.tag_id where mrt.merchant_rule_id = 1"
                    )
                )
                == "Recurring"
            )
    finally:
        engine.dispose()


def test_workspace_rule_downgrade_backfills_typed_only_merchant_pattern(tmp_path: Path) -> None:
    engine = migration_engine(tmp_path)
    try:
        upgrade(engine, "0013_workspace_rule_engine")
        with engine.begin() as connection:
            connection.execute(
                text("insert into users (google_sub, email) values ('owner', 'owner@example.com')")
            )
            connection.execute(
                text(
                    "insert into workspaces (id, name, is_personal, owner_id) "
                    "values (1, 'Personal', 1, 1)"
                )
            )
            result = connection.execute(
                text(
                    "insert into merchant_rules "
                    "(workspace_id, name, enabled, priority, condition_version, condition_json, "
                    "lock_version) values (1, 'Typed only', 1, 0, 1, :condition_json, 1)"
                ),
                {
                    "condition_json": json.dumps(
                        {
                            "field": "merchant_key",
                            "operator": "contains",
                            "type": "predicate",
                            "value": "TYPED ONLY",
                            "version": 1,
                        }
                    )
                },
            )
            typed_rule_id = result.lastrowid

        downgrade(engine, "0012_tax_refund_and_installment_tags")

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("select merchant_pattern from merchant_rules where id = :id"),
                    {"id": typed_rule_id},
                )
                == f"__workspace_rule_{typed_rule_id}__"
            )
    finally:
        engine.dispose()
