import hashlib
from collections import defaultdict
from collections.abc import Sequence, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Transaction
from app.imports.types import FingerprintedTransaction, NormalizedTransaction

LOOKUP_CHUNK_SIZE = 500


def fingerprint_transactions(
    rows: Sequence[NormalizedTransaction],
) -> tuple[FingerprintedTransaction, ...]:
    """Create stable fingerprints while preserving legitimate repeated rows."""
    occurrences: dict[tuple[object, ...], int] = defaultdict(int)
    fingerprinted: list[FingerprintedTransaction] = []
    for item in rows:
        key = (item.transaction_date, item.amount_cents, item.normalized_merchant)
        occurrences[key] += 1
        occurrence = occurrences[key]
        payload = (
            f"v1\n{item.transaction_date.isoformat()}\n{item.amount_cents}\n"
            f"{item.normalized_merchant}\n{occurrence}"
        )
        fingerprinted.append(
            FingerprintedTransaction(
                transaction=item,
                occurrence=occurrence,
                fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(fingerprinted)


def find_existing_fingerprints(
    session: Session,
    workspace_id: int,
    fingerprints: Set[str],
) -> set[str]:
    """Find transaction fingerprints only inside one authorized workspace."""
    if not fingerprints:
        return set()
    ordered = sorted(fingerprints)
    existing: set[str] = set()
    for start in range(0, len(ordered), LOOKUP_CHUNK_SIZE):
        chunk = ordered[start : start + LOOKUP_CHUNK_SIZE]
        matches = session.scalars(
            select(Transaction.duplicate_fingerprint).where(
                Transaction.workspace_id == workspace_id,
                Transaction.duplicate_fingerprint.in_(chunk),
            )
        )
        existing.update(value for value in matches if value is not None)
    return existing
