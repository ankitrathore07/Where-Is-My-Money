from datetime import date

from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountBalanceSnapshot,
    AccountStatementImport,
    UploadedFile,
    Workspace,
)


def test_pending_statement_import_round_trips_with_workspace_relationships(
    session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Northstar Brokerage",
        account_type="investment_brokerage",
        is_liability=False,
    )
    uploaded = UploadedFile(
        workspace_id=workspace.id,
        file_type="account_statement",
        storage_path=f"{workspace.id}/{'a' * 32}.csv",
        checksum="a" * 64,
        size_bytes=10,
    )
    pending = AccountStatementImport(
        workspace_id=workspace.id,
        uploaded_file=uploaded,
        account=account,
        statement_category="brokerage",
        source_checksum="a" * 64,
        candidate_fields={"balance_cents": 12_500},
        review_status="pending",
    )

    session.add(pending)
    session.commit()

    assert pending.workspace.statement_imports == [pending]
    assert uploaded.statement_imports == [pending]
    assert account.statement_imports == [pending]
    assert pending.candidate_fields == {"balance_cents": 12_500}
    assert pending.confirmed_fields is None


def test_confirmed_snapshot_links_back_to_exactly_one_statement_import(
    session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Northstar Mortgage",
        account_type="mortgage",
        is_liability=True,
    )
    uploaded = UploadedFile(
        workspace_id=workspace.id,
        file_type="account_statement",
        storage_path=f"{workspace.id}/{'b' * 32}.pdf",
        checksum="b" * 64,
        size_bytes=20,
    )
    statement_import = AccountStatementImport(
        workspace_id=workspace.id,
        uploaded_file=uploaded,
        account=account,
        statement_category="mortgage",
        source_checksum="b" * 64,
        candidate_fields={"balance_cents": 24_812_544},
        confirmed_fields={"balance_cents": 24_812_544},
        review_status="confirmed",
    )
    snapshot = AccountBalanceSnapshot(
        workspace_id=workspace.id,
        account=account,
        balance_cents=24_812_544,
        as_of_date=date(2026, 7, 31),
        source="statement_import",
        uploaded_file=uploaded,
        statement_import=statement_import,
    )

    session.add(snapshot)
    session.commit()

    assert statement_import.balance_snapshot is snapshot
    assert snapshot.statement_import is statement_import
    assert snapshot.statement_import_id == statement_import.id
