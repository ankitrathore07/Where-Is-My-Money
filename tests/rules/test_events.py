from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session

from app.categorization.events import CategorizationEventReason, record_categorization_event
from app.categorization.types import CategorizationSource
from app.db.models import Transaction, TransactionCategorizationEvent, Workspace


def _config(engine: Engine) -> Config:
    configured = Config("alembic.ini")
    configured.set_main_option("sqlalchemy.url", str(engine.url))
    return configured


def test_categorization_event_migration_is_redacted_and_round_trips(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'categorization-events.db').as_posix()}")

    @sqlalchemy_event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    try:
        command.upgrade(_config(engine), "0015_categorization_events")
        schema = inspect(engine)
        columns = {
            column["name"] for column in schema.get_columns("transaction_categorization_events")
        }
        assert columns == {
            "id",
            "workspace_id",
            "transaction_id",
            "previous_source",
            "new_source",
            "previous_rule_id",
            "new_rule_id",
            "reason",
            "created_at",
        }
        assert not columns.intersection(
            {"description", "normalized_merchant", "amount_cents", "category_id", "tag_ids"}
        )
        assert any(
            index["column_names"] == ["workspace_id", "created_at"]
            for index in schema.get_indexes("transaction_categorization_events")
        )

        command.downgrade(_config(engine), "0014_rule_application_runs")
        assert "transaction_categorization_events" not in inspect(engine).get_table_names()
        assert "rule_application_runs" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def _transaction(session: Session, workspace_id: int) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, 18, tzinfo=UTC),
        description="PRIVATE MERCHANT DESCRIPTION",
        normalized_merchant="Private merchant",
        amount_cents=-12_345,
        categorization_source=CategorizationSource.UNCATEGORIZED.value,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_record_categorization_event_persists_only_redacted_attribution(
    session: Session,
    workspace: Workspace,
) -> None:
    transaction = _transaction(session, workspace.id)

    event = record_categorization_event(
        session,
        workspace_id=workspace.id,
        transaction_id=transaction.id,
        previous_source=CategorizationSource.UNCATEGORIZED,
        new_source=CategorizationSource.MANUAL,
        previous_rule_id=None,
        new_rule_id=None,
        reason=CategorizationEventReason.MANUAL_CORRECTION,
    )
    session.flush()

    assert event is session.scalar(select(TransactionCategorizationEvent))
    assert event.workspace_id == workspace.id
    assert event.transaction_id == transaction.id
    assert event.previous_source == "uncategorized"
    assert event.new_source == "manual"
    assert event.previous_rule_id is None
    assert event.new_rule_id is None
    assert event.reason == "manual_correction"
    assert event.created_at is not None
    assert not {
        "description",
        "normalized_merchant",
        "amount_cents",
        "category_id",
        "tag_ids",
    }.intersection(TransactionCategorizationEvent.__table__.columns.keys())


def test_record_categorization_event_skips_unchanged_attribution(
    session: Session,
    workspace: Workspace,
) -> None:
    transaction = _transaction(session, workspace.id)

    event = record_categorization_event(
        session,
        workspace_id=workspace.id,
        transaction_id=transaction.id,
        previous_source=CategorizationSource.UNCATEGORIZED,
        new_source=CategorizationSource.UNCATEGORIZED,
        previous_rule_id=None,
        new_rule_id=None,
        reason=CategorizationEventReason.IMPORT_COMMIT,
    )

    assert event is None
    assert session.scalars(select(TransactionCategorizationEvent)).all() == []
