from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CashFlowSummary:
    start_date: date
    end_date: date
    income_cents: int
    spending_cents: int
    savings_cents: int
    savings_rate_basis_points: int | None
    needs_review_count: int


@dataclass(frozen=True)
class HouseholdSpendingSummary:
    start_date: date
    end_date: date
    total_paid_cents: int
    normalized_monthly_cents: int
    transaction_count: int
    needs_review_count: int
