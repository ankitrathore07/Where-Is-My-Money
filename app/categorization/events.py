from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.categorization.types import CategorizationSource
from app.db.models import MerchantRule, Transaction, TransactionCategorizationEvent


class CategorizationEventReason(StrEnum):
    MANUAL_CORRECTION = "manual_correction"
    IMPORT_COMMIT = "import_commit"
    HISTORICAL_APPLICATION = "historical_application"


class CategorizationEventScopeError(ValueError):
    """Raised when an event references a resource outside its workspace."""


@dataclass(frozen=True)
class CategorizationEventDraft:
    workspace_id: int
    transaction_id: int
    previous_source: str | CategorizationSource
    new_source: str | CategorizationSource
    previous_rule_id: int | None
    new_rule_id: int | None
    reason: CategorizationEventReason


def _source_value(source: str | CategorizationSource) -> str:
    try:
        return CategorizationSource(source).value
    except ValueError as exc:
        raise ValueError("Unsupported categorization source") from exc


def _positive_id(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def record_categorization_events(
    session: Session,
    drafts: tuple[CategorizationEventDraft, ...],
) -> tuple[TransactionCategorizationEvent, ...]:
    """Validate resource scope in batches, then stage changed attribution events."""
    normalized: list[CategorizationEventDraft] = []
    for draft in drafts:
        workspace_id = _positive_id(draft.workspace_id, "workspace_id")
        transaction_id = _positive_id(draft.transaction_id, "transaction_id")
        previous_rule_id = (
            _positive_id(draft.previous_rule_id, "previous_rule_id")
            if draft.previous_rule_id is not None
            else None
        )
        new_rule_id = (
            _positive_id(draft.new_rule_id, "new_rule_id")
            if draft.new_rule_id is not None
            else None
        )
        previous_source = _source_value(draft.previous_source)
        new_source = _source_value(draft.new_source)
        reason = CategorizationEventReason(draft.reason)
        if previous_source == new_source and previous_rule_id == new_rule_id:
            continue
        normalized.append(
            CategorizationEventDraft(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                previous_source=previous_source,
                new_source=new_source,
                previous_rule_id=previous_rule_id,
                new_rule_id=new_rule_id,
                reason=reason,
            )
        )
    if not normalized:
        return ()

    transaction_pairs = {(item.transaction_id, item.workspace_id) for item in normalized}
    rule_pairs = {
        (rule_id, item.workspace_id)
        for item in normalized
        for rule_id in (item.previous_rule_id, item.new_rule_id)
        if rule_id is not None
    }
    with session.no_autoflush:
        scoped_transactions = set(
            session.execute(
                select(Transaction.id, Transaction.workspace_id)
                .where(tuple_(Transaction.id, Transaction.workspace_id).in_(transaction_pairs))
                .with_for_update()
            )
        )
        scoped_rules = (
            set(
                session.execute(
                    select(MerchantRule.id, MerchantRule.workspace_id)
                    .where(tuple_(MerchantRule.id, MerchantRule.workspace_id).in_(rule_pairs))
                    .with_for_update()
                )
            )
            if rule_pairs
            else set()
        )
    if scoped_transactions != transaction_pairs or scoped_rules != rule_pairs:
        raise CategorizationEventScopeError("Categorization event resource not found")

    events = tuple(
        TransactionCategorizationEvent(
            workspace_id=item.workspace_id,
            transaction_id=item.transaction_id,
            previous_source=str(item.previous_source),
            new_source=str(item.new_source),
            previous_rule_id=item.previous_rule_id,
            new_rule_id=item.new_rule_id,
            reason=item.reason.value,
        )
        for item in normalized
    )
    session.add_all(events)
    return events


def record_categorization_event(
    session: Session,
    *,
    workspace_id: int,
    transaction_id: int,
    previous_source: str | CategorizationSource,
    new_source: str | CategorizationSource,
    previous_rule_id: int | None,
    new_rule_id: int | None,
    reason: CategorizationEventReason,
) -> TransactionCategorizationEvent | None:
    """Stage one redacted event through the workspace-scoped batch boundary."""
    events = record_categorization_events(
        session,
        (
            CategorizationEventDraft(
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                previous_source=previous_source,
                new_source=new_source,
                previous_rule_id=previous_rule_id,
                new_rule_id=new_rule_id,
                reason=reason,
            ),
        ),
    )
    return events[0] if events else None
