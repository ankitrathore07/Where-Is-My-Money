from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import Account, AccountBalanceSnapshot, Workspace
from app.payslips.extraction import ExtractedText
from app.statement_imports.service import (
    StatementImportError,
    confirm_statement_import,
    ingest_one_statement,
    list_compatible_accounts,
)
from app.statement_imports.storage import StatementStorageError, StatementUploadStore
from app.statement_imports.types import StatementReviewValidationError

CSV_BYTES = (
    b"account_name,institution,account_last_four,total_balance,as_of_date\n"
    b"Northstar Mortgage,Northstar Home Loans,7742,248125.44,2026-07-31\n"
)


class UnusedExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        raise AssertionError("CSV ingestion must not use document extraction")


def _pending(tmp_path: Path, session: Session, workspace: Workspace, retention: str = "retain"):
    return ingest_one_statement(
        session,
        StatementUploadStore(tmp_path),
        UnusedExtractor(),
        workspace,
        "mortgage",
        "mortgage.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        retention,
    )


def _form(account: Account) -> dict[str, str]:
    return {
        "account_id": str(account.id),
        "account_name": "Reviewed Mortgage",
        "institution": "Reviewed Servicer",
        "account_last_four": "7742",
        "total_balance": "248000.01",
        "as_of_date": "2026-08-01",
    }


def test_confirmation_saves_exact_reviewed_snapshot_and_fields(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Mortgage",
        account_type="mortgage",
        is_liability=True,
    )
    session.add(account)
    session.commit()
    pending = _pending(tmp_path, session, workspace)

    result = confirm_statement_import(
        session,
        StatementUploadStore(tmp_path),
        pending,
        _form(account),
        today=date(2026, 8, 11),
    )

    assert result.snapshot.balance_cents == 24_800_001
    assert result.snapshot.as_of_date == date(2026, 8, 1)
    assert result.snapshot.source == "statement_import"
    assert result.snapshot.workspace_id == workspace.id
    assert result.snapshot.uploaded_file_id == pending.uploaded_file_id
    assert result.snapshot.statement_import_id == pending.id
    assert pending.confirmed_fields["account_name"] == "Reviewed Mortgage"
    assert pending.account_id == account.id
    assert pending.review_status == "confirmed"
    assert result.already_confirmed is False


def test_repeated_confirmation_returns_existing_snapshot(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id, name="Mortgage", account_type="mortgage", is_liability=True
    )
    session.add(account)
    session.commit()
    pending = _pending(tmp_path, session, workspace)
    first = confirm_statement_import(
        session, StatementUploadStore(tmp_path), pending, _form(account), today=date(2026, 8, 11)
    )
    second = confirm_statement_import(
        session, StatementUploadStore(tmp_path), pending, _form(account), today=date(2026, 8, 11)
    )
    assert second.snapshot.id == first.snapshot.id
    assert second.already_confirmed is True
    assert session.query(AccountBalanceSnapshot).count() == 1


def test_compatible_accounts_are_scoped_and_sorted(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    session.add_all(
        [
            Account(
                workspace_id=workspace.id, name="Zulu", account_type="mortgage", is_liability=True
            ),
            Account(
                workspace_id=workspace.id, name="alpha", account_type="mortgage", is_liability=True
            ),
            Account(
                workspace_id=workspace.id,
                name="Checking",
                account_type="checking",
                is_liability=False,
            ),
            Account(
                workspace_id=other_workspace.id,
                name="SECRET",
                account_type="mortgage",
                is_liability=True,
            ),
        ]
    )
    session.commit()
    assert [
        account.name for account in list_compatible_accounts(session, workspace.id, "mortgage")
    ] == ["alpha", "Zulu"]


def test_confirmation_rejects_foreign_or_incompatible_account(
    tmp_path: Path, session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    pending = _pending(tmp_path, session, workspace)
    for account in (
        Account(
            workspace_id=workspace.id, name="Checking", account_type="checking", is_liability=False
        ),
        Account(
            workspace_id=other_workspace.id,
            name="Foreign",
            account_type="mortgage",
            is_liability=True,
        ),
    ):
        session.add(account)
        session.commit()
        with pytest.raises(StatementImportError) as error:
            confirm_statement_import(
                session,
                StatementUploadStore(tmp_path),
                pending,
                _form(account),
                today=date(2026, 8, 11),
            )
        assert error.value.code == "account_not_found"
    assert session.query(AccountBalanceSnapshot).count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "not-an-id"),
        ("account_name", ""),
        ("account_last_four", "12A4"),
        ("total_balance", "-1.00"),
        ("as_of_date", "2026-08-12"),
    ],
)
def test_confirmation_revalidates_every_editable_field(
    tmp_path: Path,
    session: Session,
    workspace: Workspace,
    field: str,
    value: str,
) -> None:
    account = Account(
        workspace_id=workspace.id, name="Mortgage", account_type="mortgage", is_liability=True
    )
    session.add(account)
    session.commit()
    pending = _pending(tmp_path, session, workspace)
    form = _form(account)
    form[field] = value
    with pytest.raises(StatementReviewValidationError) as error:
        confirm_statement_import(
            session, StatementUploadStore(tmp_path), pending, form, today=date(2026, 8, 11)
        )
    assert field in error.value.field_errors


def test_delete_after_confirmation_removes_source(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id, name="Mortgage", account_type="mortgage", is_liability=True
    )
    session.add(account)
    session.commit()
    pending = _pending(tmp_path, session, workspace, "delete_after_import")
    source_path = pending.uploaded_file.storage_path
    result = confirm_statement_import(
        session, StatementUploadStore(tmp_path), pending, _form(account), today=date(2026, 8, 11)
    )
    assert result.cleanup_failed is False
    assert pending.uploaded_file.deleted is True
    with pytest.raises(StatementStorageError):
        StatementUploadStore(tmp_path).read(source_path)
