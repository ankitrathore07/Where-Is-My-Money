from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models import Transaction, Workspace
from app.imports.duplicates import (
    find_existing_fingerprints,
    fingerprint_transactions,
)
from app.imports.types import NormalizedTransaction


def row(number: int, merchant: str = "EXAMPLE MARKET") -> NormalizedTransaction:
    return NormalizedTransaction(
        row_number=number,
        transaction_date=date(2026, 8, 1),
        description=merchant.title(),
        normalized_merchant=merchant,
        amount_cents=-1234,
    )


def test_same_rows_produce_stable_versioned_fingerprints() -> None:
    first = fingerprint_transactions((row(2), row(3)))
    second = fingerprint_transactions((row(2), row(3)))

    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert [item.occurrence for item in first] == [1, 2]
    assert first[0].fingerprint != first[1].fingerprint
    assert len(first[0].fingerprint) == 64


def test_changed_normalized_merchant_changes_the_fingerprint() -> None:
    original = fingerprint_transactions((row(2),))[0].fingerprint
    changed = fingerprint_transactions((row(2, "OTHER MARKET"),))[0].fingerprint

    assert changed != original


def test_existing_lookup_is_workspace_scoped(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    fingerprint = fingerprint_transactions((row(2),))[0].fingerprint
    session.add(
        Transaction(
            workspace_id=other_workspace.id,
            date=datetime(2026, 8, 1, tzinfo=UTC),
            description="Example Market",
            normalized_merchant="EXAMPLE MARKET",
            amount_cents=-1234,
            duplicate_fingerprint=fingerprint,
        )
    )
    session.commit()

    assert find_existing_fingerprints(session, workspace.id, {fingerprint}) == set()
    assert find_existing_fingerprints(session, other_workspace.id, {fingerprint}) == {fingerprint}


def test_empty_lookup_returns_without_querying(session: Session, workspace: Workspace) -> None:
    assert find_existing_fingerprints(session, workspace.id, set()) == set()
