from enum import StrEnum

from sqlalchemy.orm import Session

from app.categorization.types import CategorizationSource
from app.db.models import TransactionCategorizationEvent


class CategorizationEventReason(StrEnum):
    MANUAL_CORRECTION = "manual_correction"
    IMPORT_COMMIT = "import_commit"
    HISTORICAL_APPLICATION = "historical_application"


def _source_value(source: str | CategorizationSource) -> str:
    try:
        return CategorizationSource(source).value
    except ValueError as exc:
        raise ValueError("Unsupported categorization source") from exc


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
    """Stage a redacted event when source or rule attribution changed."""
    previous_source_value = _source_value(previous_source)
    new_source_value = _source_value(new_source)
    if previous_source_value == new_source_value and previous_rule_id == new_rule_id:
        return None
    event = TransactionCategorizationEvent(
        workspace_id=workspace_id,
        transaction_id=transaction_id,
        previous_source=previous_source_value,
        new_source=new_source_value,
        previous_rule_id=previous_rule_id,
        new_rule_id=new_rule_id,
        reason=CategorizationEventReason(reason).value,
    )
    session.add(event)
    return event
