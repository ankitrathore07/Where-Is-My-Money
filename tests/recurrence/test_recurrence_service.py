from datetime import date

from app.recurrence.service import RecurrenceCandidate, RecurrenceObservation, detect_recurrence


def _candidate(day: date, amount_cents: int = -1_599) -> RecurrenceCandidate:
    return RecurrenceCandidate(1, day, "Netflix", amount_cents)


def _observation(day: date, amount_cents: int = -1_599) -> RecurrenceObservation:
    return RecurrenceObservation(day, "Netflix", amount_cents)


def test_detects_monthly_recurrence_with_calendar_and_amount_drift() -> None:
    suggestion = detect_recurrence(
        _candidate(date(2026, 3, 31), -1_625),
        (
            _observation(date(2026, 1, 31), -1_599),
            _observation(date(2026, 2, 28), -1_599),
        ),
    )

    assert suggestion is not None
    assert suggestion.billing_period_months == 1
    assert suggestion.occurrence_count == 3
    assert suggestion.confidence >= 0.9


def test_requires_three_occurrences() -> None:
    suggestion = detect_recurrence(
        _candidate(date(2026, 2, 28)),
        (_observation(date(2026, 1, 31)),),
    )

    assert suggestion is None


def test_rejects_different_merchant_amount_or_irregular_dates() -> None:
    history = (
        RecurrenceObservation(date(2026, 1, 15), "Netflix", -3_999),
        RecurrenceObservation(date(2026, 2, 3), "Other service", -1_599),
        RecurrenceObservation(date(2026, 2, 20), "Netflix", -1_599),
    )

    assert detect_recurrence(_candidate(date(2026, 3, 31)), history) is None


def test_detects_quarterly_recurrence_without_calling_it_a_subscription() -> None:
    suggestion = detect_recurrence(
        RecurrenceCandidate(7, date(2026, 7, 15), "Insurance Co", -12_000),
        (
            RecurrenceObservation(date(2026, 1, 15), "Insurance Co", -12_000),
            RecurrenceObservation(date(2026, 4, 15), "Insurance Co", -12_000),
        ),
    )

    assert suggestion is not None
    assert suggestion.billing_period_months == 3


def test_income_is_not_treated_as_a_recurring_payment() -> None:
    assert (
        detect_recurrence(
            RecurrenceCandidate(1, date(2026, 3, 1), "Employer", 100_000),
            (
                RecurrenceObservation(date(2026, 1, 1), "Employer", 100_000),
                RecurrenceObservation(date(2026, 2, 1), "Employer", 100_000),
            ),
        )
        is None
    )
