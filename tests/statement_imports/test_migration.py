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


def test_statement_import_migration_round_trips_and_enforces_unique_links(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'statement-imports.db').as_posix()}"
    configured = _config(database_url)
    command.upgrade(configured, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "account_statement_imports" in inspector.get_table_names()
        snapshot_columns = {
            column["name"] for column in inspector.get_columns("account_balance_snapshots")
        }
        assert "statement_import_id" in snapshot_columns
        import_constraints = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("account_statement_imports")
        }
        assert "uix_statement_import_workspace_category_checksum" in import_constraints
        snapshot_indexes = {
            index["name"]: index for index in inspector.get_indexes("account_balance_snapshots")
        }
        assert snapshot_indexes["uix_balance_snapshot_statement_import_id"]["unique"] == 1

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
                    "insert into accounts (workspace_id, name, account_type, is_liability) "
                    "values (1, 'Mortgage', 'mortgage', 1)"
                )
            )
            connection.execute(
                text(
                    "insert into uploaded_files "
                    "(workspace_id, file_type, storage_path, checksum, size_bytes) "
                    "values (1, 'account_statement', '1/source.pdf', :checksum, 20)"
                ),
                {"checksum": "a" * 64},
            )
            connection.execute(
                text(
                    "insert into account_statement_imports "
                    "(workspace_id, uploaded_file_id, account_id, statement_category, "
                    "source_checksum, candidate_fields, review_status) "
                    "values (1, 1, 1, 'mortgage', :checksum, '{}', 'pending')"
                ),
                {"checksum": "a" * 64},
            )
            connection.execute(
                text(
                    "insert into account_balance_snapshots "
                    "(workspace_id, account_id, balance_cents, as_of_date, source, "
                    "uploaded_file_id, statement_import_id) "
                    "values (1, 1, 10000, '2026-07-31', 'statement_import', 1, 1)"
                )
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "insert into account_statement_imports "
                        "(workspace_id, uploaded_file_id, statement_category, source_checksum, "
                        "candidate_fields, review_status) "
                        "values (1, 1, 'mortgage', :checksum, '{}', 'pending')"
                    ),
                    {"checksum": "a" * 64},
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "insert into account_balance_snapshots "
                        "(workspace_id, account_id, balance_cents, as_of_date, source, "
                        "statement_import_id) "
                        "values (1, 1, 9999, '2026-08-01', 'statement_import', 1)"
                    )
                )
    finally:
        engine.dispose()

    command.downgrade(configured, "0008_unique_payslip_income")
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "account_statement_imports" not in inspector.get_table_names()
        assert "statement_import_id" not in {
            column["name"] for column in inspector.get_columns("account_balance_snapshots")
        }
    finally:
        engine.dispose()

    command.upgrade(configured, "head")
