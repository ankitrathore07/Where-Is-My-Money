from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Account, Category, Transaction, Workspace
from app.imports.duplicates import fingerprint_transactions
from app.imports.normalization import normalize_source_row
from app.imports.parser import parse_csv_bytes
from app.imports.providers.registry import resolve_provider_profile
from app.imports.service import build_review, create_csv_import
from app.imports.storage import LocalUploadStore

FIXTURE_PATH = Path("tests/fixtures/statements/synthetic_capital_one_credit_card.csv")


def _capital_one_account(session: Session, workspace: Workspace, account_type: str) -> Account:
    account = Account(
        workspace_id=workspace.id,
        name="Capital One Synthetic",
        account_type=account_type,
        institution_key="capital_one",
        institution="Capital One",
        is_liability=account_type == "credit_card",
    )
    session.add(account)
    session.flush()
    return account


def test_capital_one_fixture_enters_review_with_iso_dates_and_signed_amounts(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _capital_one_account(session, workspace, "credit_card")
    session.add(Category(workspace_id=None, name="Uncategorized", kind="expense"))
    session.commit()
    store = LocalUploadStore(tmp_path)

    result = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(FIXTURE_PATH.read_bytes()),
        "retain",
        account=account,
    )
    review = build_review(session, store, result.job)

    assert result.job.status == "reviewing"
    assert result.job.column_mapping == {
        "date_column": "Transaction Date",
        "description_column": "Description",
        "amount_mode": "split",
        "amount_column": None,
        "debit_column": "Debit",
        "credit_column": "Credit",
        "date_format": "iso",
        "amount_sign": "as_is",
    }
    assert [
        (row.date_value, row.description_value, row.amount_value, row.included)
        for row in review.rows
    ] == [
        ("2026-04-26", "CAPITAL ONE MOBILE PYMT", "16.76", True),
        ("2026-04-16", "NORTHSTAR CREAMERY", "-16.76", True),
        ("2026-04-08", "EXAMPLE MARKET", "-9.99", True),
        ("2026-04-07", "TST* FICTIONAL CAFE", "-6.48", True),
    ]
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_capital_one_fixture_marks_existing_transaction_as_duplicate(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _capital_one_account(session, workspace, "credit_card")
    session.add(Category(workspace_id=None, name="Uncategorized", kind="expense"))
    session.commit()
    document = parse_csv_bytes(FIXTURE_PATH.read_bytes())
    resolution = resolve_provider_profile(
        account.institution_key,
        account.account_type,
        ".csv",
        document.headers,
    )
    assert resolution.mapping is not None
    normalized = normalize_source_row(document.rows[1], resolution.mapping)
    fingerprinted = fingerprint_transactions((normalized,))[0]
    session.add(
        Transaction(
            workspace_id=workspace.id,
            date=datetime.combine(normalized.transaction_date, datetime.min.time(), tzinfo=UTC),
            description=normalized.description,
            normalized_merchant=normalized.normalized_merchant,
            amount_cents=normalized.amount_cents,
            duplicate_fingerprint=fingerprinted.fingerprint,
        )
    )
    session.commit()
    store = LocalUploadStore(tmp_path)

    result = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(FIXTURE_PATH.read_bytes()),
        "retain",
        account=account,
    )
    review = build_review(session, store, result.job)

    assert review.duplicate_rows == 1
    assert review.rows[1].duplicate is True
    assert review.rows[1].included is False


def test_capital_one_fixture_for_checking_account_keeps_generic_mapping(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _capital_one_account(session, workspace, "checking")

    result = create_csv_import(
        session,
        LocalUploadStore(tmp_path),
        workspace,
        BytesIO(FIXTURE_PATH.read_bytes()),
        "retain",
        account=account,
    )

    assert result.job.status == "awaiting_mapping"
    assert result.job.column_mapping is None
