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

FIXTURE_PATH = Path("tests/fixtures/statements/synthetic_citi_costco_credit_card.csv")


def _citi_account(session: Session, workspace: Workspace, account_type: str) -> Account:
    account = Account(
        workspace_id=workspace.id,
        name="Citi Synthetic",
        account_type=account_type,
        institution_key="citi",
        institution="Citi",
        is_liability=account_type == "credit_card",
    )
    session.add(account)
    session.flush()
    return account


def test_citi_fixture_enters_review_with_mdy_dates_and_signed_amounts(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _citi_account(session, workspace, "credit_card")
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
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "split",
        "amount_column": None,
        "debit_column": "Debit",
        "credit_column": "Credit",
        "date_format": "mdy",
        "amount_sign": "as_is",
    }
    assert [
        (row.date_value, row.description_value, row.amount_value, row.included)
        for row in review.rows
    ] == [
        ("2026-08-08", "FICTIONAL WAREHOUSE", "-76.31", True),
        ("2026-07-15", "CREDIT INTEREST CHARGES", "0.20", True),
        ("2026-07-15", "ONLINE PAYMENT, THANK YOU", "382.94", True),
        ("2025-12-16", "FICTIONAL WAREHOUSE", "43.29", True),
    ]
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_citi_fixture_marks_existing_transaction_as_duplicate(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _citi_account(session, workspace, "credit_card")
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
    normalized = normalize_source_row(document.rows[0], resolution.mapping)
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
    assert review.rows[0].duplicate is True
    assert review.rows[0].included is False


def test_citi_fixture_for_checking_account_keeps_generic_mapping(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    account = _citi_account(session, workspace, "checking")

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
