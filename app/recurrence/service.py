"""Conservative recurring-payment detection from transaction history."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categorization.normalization import merchant_key
from app.db.models import Category, Transaction

_SUPPORTED_PERIODS = (1, 3, 6, 12)
_MINIMUM_OCCURRENCES = 3
_BILLING_DAY_TOLERANCE = 5


@dataclass(frozen=True)
class RecurrenceObservation:
    transaction_date: date
    merchant: str
    amount_cents: int


@dataclass(frozen=True)
class RecurrenceCandidate:
    row_number: int
    transaction_date: date
    merchant: str
    amount_cents: int


@dataclass(frozen=True)
class RecurrenceSuggestion:
    billing_period_months: int
    occurrence_count: int
    confidence: float


def _amount_tolerance(amount_cents: int) -> int:
    """Allow minor price drift while avoiding unrelated same-merchant purchases."""
    return max(100, round(abs(amount_cents) * 0.02))


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _billing_day_matches(earlier: date, later: date) -> bool:
    expected_day = min(earlier.day, monthrange(later.year, later.month)[1])
    return abs(later.day - expected_day) <= _BILLING_DAY_TOLERANCE


def _period_matches(dates: tuple[date, ...], period_months: int) -> bool:
    return all(
        _months_between(earlier, later) == period_months
        and _billing_day_matches(earlier, later)
        for earlier, later in zip(dates, dates[1:], strict=False)
    )


def detect_recurrence(
    candidate: RecurrenceCandidate,
    observations: tuple[RecurrenceObservation, ...],
) -> RecurrenceSuggestion | None:
    """Infer a supported cadence for one expense using same-merchant history."""
    if candidate.amount_cents >= 0:
        return None
    candidate_merchant = merchant_key(candidate.merchant)
    if not candidate_merchant:
        return None

    tolerance = _amount_tolerance(candidate.amount_cents)
    matching = (
        candidate,
        *(
            observation
            for observation in observations
            if observation.amount_cents < 0
            and merchant_key(observation.merchant) == candidate_merchant
            and abs(abs(observation.amount_cents) - abs(candidate.amount_cents)) <= tolerance
        ),
    )
    dates = tuple(sorted({observation.transaction_date for observation in matching}))
    if len(dates) < _MINIMUM_OCCURRENCES:
        return None

    for period_months in _SUPPORTED_PERIODS:
        if not _period_matches(dates, period_months):
            continue
        largest_amount_delta = max(
            abs(abs(observation.amount_cents) - abs(candidate.amount_cents))
            for observation in matching
        )
        confidence = max(
            0.0,
            1.0 - (largest_amount_delta / max(abs(candidate.amount_cents), 1)),
        )
        return RecurrenceSuggestion(period_months, len(dates), confidence)
    return None


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def suggest_recurrences(
    session: Session,
    workspace_id: int,
    candidates: tuple[RecurrenceCandidate, ...],
) -> dict[int, RecurrenceSuggestion]:
    """Compare import candidates with expense history and their current import batch."""
    if not candidates:
        return {}

    history = tuple(
        RecurrenceObservation(
            _as_date(transaction_date),
            normalized_merchant or description,
            amount_cents,
        )
        for transaction_date, normalized_merchant, description, amount_cents in session.execute(
            select(
                Transaction.date,
                Transaction.normalized_merchant,
                Transaction.description,
                Transaction.amount_cents,
            )
            .join(Category, Transaction.category_id == Category.id)
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.amount_cents < 0,
                Category.kind == "expense",
            )
        )
    )
    batch_observations = tuple(
        RecurrenceObservation(
            candidate.transaction_date,
            candidate.merchant,
            candidate.amount_cents,
        )
        for candidate in candidates
    )
    suggestions: dict[int, RecurrenceSuggestion] = {}
    for candidate in candidates:
        suggestion = detect_recurrence(candidate, (*history, *batch_observations))
        if suggestion is not None:
            suggestions[candidate.row_number] = suggestion
    return suggestions
