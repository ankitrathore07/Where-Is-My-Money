from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import MerchantRule, RuleApplicationRun, Workspace
from app.rules import application_tokens
from app.rules.application_tokens import ApplicationTokenPayload


def _config(engine: Engine) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", str(engine.url))
    return configured


def _engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{(tmp_path / 'rule-applications.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


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
        "selection_json": '{"normalized_filters":{},"selected_transaction_ids":[2,7]}',
        "preview_digest": "a" * 64,
        "matched_count": 4,
        "changed_count": 2,
        "unchanged_count": 1,
        "manual_skip_count": 1,
        "conflict_skip_count": 0,
    }


def _canonical_selection(
    *, selected_transaction_ids: tuple[int, ...] = (2, 7)
) -> dict[str, object]:
    return application_tokens.canonical_application_selection(
        ApplicationTokenPayload(
            workspace_id=1,
            merchant_rule_id=1,
            rule_lock_version=3,
            selected_transaction_ids=selected_transaction_ids,
            state_digest="a" * 64,
            normalized_filters={},
        )
    )


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
        {"preview_digest": "a" * 63},
        {"preview_digest": "A" * 64},
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
        selection_json=_canonical_selection(),
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


def test_application_run_migration_enforces_database_foreign_key_actions(tmp_path: Path) -> None:
    """Break if database FK actions differ from redacted-audit ownership semantics."""
    engine = _engine(tmp_path)
    try:
        command.upgrade(_config(engine), "0014_rule_application_runs")
        schema = inspect(engine)
        foreign_keys = {
            tuple(foreign_key["constrained_columns"]): foreign_key
            for foreign_key in schema.get_foreign_keys("rule_application_runs")
        }
        assert foreign_keys[("workspace_id",)]["referred_table"] == "workspaces"
        assert foreign_keys[("workspace_id",)]["options"] == {"ondelete": "CASCADE"}
        assert foreign_keys[("merchant_rule_id",)]["referred_table"] == "merchant_rules"
        assert foreign_keys[("merchant_rule_id",)]["options"] == {"ondelete": "SET NULL"}
        assert foreign_keys[("initiated_by_user_id",)]["referred_table"] == "users"
        assert foreign_keys[("initiated_by_user_id",)]["options"] == {}

        _seed_rule(engine)
        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            connection.execute(
                text(
                    "insert into rule_application_runs "
                    "(workspace_id, merchant_rule_id, initiated_by_user_id, rule_name_snapshot, "
                    "rule_lock_version, status, selection_json, preview_digest) values "
                    "(:workspace_id, :merchant_rule_id, :initiated_by_user_id, "
                    ":rule_name_snapshot, :rule_lock_version, :status, :selection_json, "
                    ":preview_digest)"
                ),
                _valid_run_values(),
            )
            connection.execute(text("delete from merchant_rules where id = 1"))
            assert (
                connection.scalar(text("select merchant_rule_id from rule_application_runs"))
                is None
            )
            connection.execute(text("delete from workspaces where id = 1"))
            assert connection.scalar(text("select count(*) from rule_application_runs")) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("foreign_key", "invalid_id"),
    [
        ("workspace_id", 999),
        ("merchant_rule_id", 999),
        ("initiated_by_user_id", 999),
    ],
)
def test_application_run_migration_rejects_invalid_foreign_references(
    tmp_path: Path, foreign_key: str, invalid_id: int
) -> None:
    """Break if a run can reference a workspace, rule, or user that does not exist."""
    engine = _engine(tmp_path)
    try:
        command.upgrade(_config(engine), "0014_rule_application_runs")
        _seed_rule(engine)
        values = _valid_run_values() | {foreign_key: invalid_id}
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "insert into rule_application_runs "
                    "(workspace_id, merchant_rule_id, initiated_by_user_id, rule_name_snapshot, "
                    "rule_lock_version, status, selection_json, preview_digest) values "
                    "(:workspace_id, :merchant_rule_id, :initiated_by_user_id, "
                    ":rule_name_snapshot, :rule_lock_version, :status, :selection_json, "
                    ":preview_digest)"
                ),
                values,
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "selection_json",
    [
        {"normalized_filters": {"description": "PRIVATE"}, "selected_transaction_ids": [2]},
        {"normalized_filters": {"amount_cents": -500}, "selected_transaction_ids": [2]},
        {
            "normalized_filters": {"condition_json": {"type": "all"}},
            "selected_transaction_ids": [2],
        },
        {"normalized_filters": {}, "selected_transaction_ids": [2], "token": "signed"},
    ],
)
def test_application_run_model_rejects_unvalidated_selection_json(
    selection_json: dict[str, object],
) -> None:
    """Break if direct ORM assignment can bypass the canonical redacted selection boundary."""
    with pytest.raises(ValueError):
        RuleApplicationRun(selection_json=selection_json)


def test_application_run_model_accepts_only_canonical_application_selection() -> None:
    """Break if the supported model path cannot persist the canonical token-derived selection."""
    payload = ApplicationTokenPayload(
        workspace_id=1,
        merchant_rule_id=1,
        rule_lock_version=1,
        selected_transaction_ids=(7, 2),
        state_digest="c" * 64,
        normalized_filters={"direction": "income", "account_id": 3},
    )
    run = RuleApplicationRun(
        selection_json=application_tokens.canonical_application_selection(payload)
    )

    assert run.selection_json == {
        "normalized_filters": {"account_id": 3, "direction": "income"},
        "selected_transaction_ids": [2, 7],
    }


def test_application_run_canonical_selection_cannot_be_mutated_after_validation() -> None:
    """Break if prohibited data can be added after the ORM assignment validator has run."""
    selection = _canonical_selection()
    run = RuleApplicationRun(selection_json=selection)

    with pytest.raises(TypeError):
        run.selection_json["token"] = "signed-preview-token"
    with pytest.raises(TypeError):
        run.selection_json["normalized_filters"]["direction"] = "expense"
    with pytest.raises(TypeError):
        run.selection_json["selected_transaction_ids"].append(99)


@pytest.mark.parametrize(
    "preview_digest",
    [
        "a" * 63,
        "A" * 64,
        "g" * 64,
        "signed.preview.token".ljust(64, "x"),
    ],
)
def test_application_run_model_rejects_noncanonical_preview_digest(
    preview_digest: str,
) -> None:
    """Break if a short, uppercase, nonhex, or token-shaped value can masquerade as a digest."""
    with pytest.raises(ValueError):
        RuleApplicationRun(preview_digest=preview_digest)
