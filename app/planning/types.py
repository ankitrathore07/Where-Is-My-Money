from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class BudgetSuggestion:
    monthly_spend_cents: tuple[int, int, int]
    median_cents: int
    suggested_cents: int


@dataclass(frozen=True)
class BudgetLine:
    category_id: int
    category_name: str
    budget_id: int | None
    limit_cents: int | None
    spent_cents: int
    remaining_cents: int | None
    suggestion: BudgetSuggestion | None


@dataclass(frozen=True)
class BudgetMonthReport:
    period_month: date
    source_start: date
    source_end: date
    lines: tuple[BudgetLine, ...]


@dataclass(frozen=True)
class GoalInput:
    name: str
    target_amount_cents: int
    current_amount_cents: int
    target_date: date | None
    monthly_contribution_cents: int | None


@dataclass(frozen=True)
class GoalProjection:
    goal_id: int | None
    name: str
    target_amount_cents: int
    current_amount_cents: int
    remaining_cents: int
    supplied_target_date: date | None
    supplied_monthly_contribution_cents: int | None
    calculated_target_date: date | None
    calculated_monthly_contribution_cents: int | None
    contribution_months: int | None
    status: str
