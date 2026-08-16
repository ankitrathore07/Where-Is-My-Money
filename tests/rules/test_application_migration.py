from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import MerchantRule, RuleApplicationRun, Workspace


def _config(engine: Engine) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", str(engine.url))
    return configured


def _engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{(tmp_path / 'rule-applications.db').as_posix()}")


def _seed_rule(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into users (id, google_sub, email) values (1, 'owner', 'owner@example.com')"
            )
        )
        connection.execute(
            text(
                "insert into workspaces (id, name, is_personal, owner_id) "
                "values (1, 'Personal', 1, 1)"
            )
        )
        connection.execute(
            text(
                "insert into merchant_rules "
                "(id, workspace_id, name, enabled, priority, condition_version, condition_json, "
                "lock_version) values (1, 1, 'Coffee', 1, 0, 1, '{}', 3)"
            )
        )


def _valid_run_values() -> dict[str, object]:
    return {
        "workspace_id": 1,
        "merchant_rule_id": 1,
        "initiated_by_user_id": 1,
        "rule_name_snapshot": "Coffee",
        "rule_lock_version": 3,
        "status": "previewed",
        "selection_json": '{"filters":{},"selected_transaction_ids":[2,7]}',
        "preview_digest": "a" * 64,
        "matched_count": 4,
        "changed_count": 2,
        "unchanged_count": 1,
        "manual_skip_count": 1,
        "conflict_skip_count": 0,
    }


def test_application_run_migration_creates_redacted_audit_and_round_trips(tmp_path: Path) -> None:
    """Break if the audit loses required metadata or adds sensitive financial payload columns."""
    engine = _engine(tmp_path)
    try:
        command.upgrade(_config(engine), "0014_rule_application_runs")
        schema = inspect(engine)
        columns = {column["name"] for column in schema.get_columns("rule_application_runs")}
        assert columns == {
            "id",
            "workspace_id",
            "merchant_rule_id",
            "initiated_by_user_id",
            "rule_name_snapshot",
            "rule_lock_version",
            "status",
            "selection_json",
            "preview_digest",
            "matched_count",
            "changed_count",
            "unchanged_count",
            "manual_skip_count",
            "conflict_skip_count",
            "created_at",
            "confirmed_at",
        }
        assert not columns.intersection(
            {
                "description",
                "merchant",
                "amount_cents",
                "token",
                "condition_json",
                "source_filename",
            }
        )
        assert any(
            index["column_names"] == ["preview_digest"]
            for index in schema.get_indexes("rule_application_runs")
        )

        _seed_rule(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into rule_application_runs "
                    "(workspace_id, merchant_rule_id, initiated_by_user_id, rule_name_snapshot, "
                    "rule_lock_version, status, selection_json, preview_digest, matched_count, "
                    "changed_count, unchanged_count, manual_skip_count, conflict_skip_count) "
                    "values (:workspace_id, :merchant_rule_id, :initiated_by_user_id, "
                    ":rule_name_snapshot, :rule_lock_version, :status, :selection_json, "
                    ":preview_digest, :matched_count, :changed_count, :unchanged_count, "
                    ":manual_skip_count, :conflict_skip_count)"
                ),
                _valid_run_values(),
            )
        with engine.connect() as connection:
            row = connection.execute(text("select * from rule_application_runs")).mappings().one()
        assert row["rule_name_snapshot"] == "Coffee"
        assert row["preview_digest"] == "a" * 64
        assert row["created_at"] is not None
        assert row["confirmed_at"] is None

        command.downgrade(_config(engine), "0013_workspace_rule_engine")
        assert "rule_application_runs" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"rule_lock_version": 0},
        {"status": "unknown"},
        {"changed_count": -1},
        {"status": "confirmed", "confirmed_at": None},
        {"status": "stale", "confirmed_at": "2026-08-15 00:00:00"},
    ],
)
def test_application_run_migration_enforces_state_and_count_checks(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    """Break if invalid audit state can be persisted and later treated as trustworthy."""
    engine = _engine(tmp_path)
    try:
        command.upgrade(_config(engine), "0014_rule_application_runs")
        _seed_rule(engine)
        values = _valid_run_values() | overrides
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "insert into rule_application_runs "
                    "(workspace_id, merchant_rule_id, initiated_by_user_id, rule_name_snapshot, "
                    "rule_lock_version, status, selection_json, preview_digest, matched_count, "
                    "changed_count, unchanged_count, manual_skip_count, conflict_skip_count, "
                    "confirmed_at) values (:workspace_id, :merchant_rule_id, "
                    ":initiated_by_user_id, :rule_name_snapshot, :rule_lock_version, :status, "
                    ":selection_json, :preview_digest, :matched_count, :changed_count, "
                    ":unchanged_count, :manual_skip_count, :conflict_skip_count, :confirmed_at)"
                ),
                values | {"confirmed_at": values.get("confirmed_at")},
            )
    finally:
        engine.dispose()


def test_application_run_model_exposes_ownership_relationships(
    session: Session, workspace: Workspace
) -> None:
    """Break if Task 10 cannot navigate the audit to its authorized workspace, user, and rule."""
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Coffee",
        condition_json={},
        lock_version=2,
    )
    session.add(rule)
    session.flush()
    run = RuleApplicationRun(
        workspace_id=workspace.id,
        merchant_rule_id=rule.id,
        initiated_by_user_id=workspace.owner_id,
        rule_name_snapshot=rule.name,
        rule_lock_version=rule.lock_version,
        status="confirmed",
        selection_json={"filters": {}, "selected_transaction_ids": [2, 7]},
        preview_digest="b" * 64,
        matched_count=2,
        changed_count=2,
        confirmed_at=datetime.now(UTC),
    )
    session.add(run)
    session.commit()

    assert run.workspace is workspace
    assert run.merchant_rule is rule
    assert run.initiated_by_user is workspace.owner
    assert workspace.rule_application_runs == [run]
    assert rule.application_runs == [run]
    assert workspace.owner.initiated_rule_application_runs == [run]

    session.delete(rule)
    session.commit()
    session.refresh(run)
    assert run.merchant_rule_id is None
