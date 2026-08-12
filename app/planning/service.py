"""Deterministic, integer-cent planning calculations and persistence."""

import calendar
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Budget, Category, SavingsGoal, Transaction
from app.planning.types import (
    BudgetLine,
    BudgetMonthReport,
    BudgetSuggestion,
    GoalInput,
    GoalProjection,
)

_MONEY_PATTERN = re.compile(r"\d+(?:\.\d{1,2})?")
MAX_MONEY_CENTS = 2**63 - 1


class PlanningValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__(next(iter(field_errors.values())))
        self.field_errors = field_errors


class PlanningNotFoundError(LookupError):
    pass


class GoalNotFoundError(PlanningNotFoundError):
    pass


def parse_money_to_cents(raw: str, *, field: str, allow_zero: bool = False) -> int:
    """Parse an ordinary decimal dollar string once into integer cents."""
    value = raw.strip()
    if not _MONEY_PATTERN.fullmatch(value):
        raise PlanningValidationError({field: "Enter a dollar amount with at most two decimals."})
    cents = int(Decimal(value) * 100)
    if cents > MAX_MONEY_CENTS:
        raise PlanningValidationError({field: "Amount is too large."})
    if cents < 0 or (cents == 0 and not allow_zero):
        qualifier = "zero or more" if allow_zero else "greater than zero"
        raise PlanningValidationError({field: f"Amount must be {qualifier}."})
    return cents


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _month_end(value: date) -> date:
    start = _month_start(value)
    return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


def _transaction_date(value: datetime) -> date:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC).date()
    return value.date()


def _accessible_expense_categories(session: Session, workspace_id: int) -> dict[int, Category]:
    rows = session.scalars(
        select(Category).where(
            Category.kind == "expense",
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    return {
        category.id: category
        for category in rows
        if category.name.strip().casefold() != "uncategorized"
    }


def _spending_by_category_and_month(
    session: Session,
    workspace_id: int,
    start: date,
    end_exclusive: date,
    accessible: dict[int, Category],
) -> dict[tuple[int, date], int]:
    totals: dict[tuple[int, date], int] = {}
    if not accessible:
        return totals
    transactions = session.scalars(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.category_id.in_(accessible),
            Transaction.amount_cents < 0,
            Transaction.date >= datetime.combine(start, time.min, tzinfo=UTC),
            Transaction.date < datetime.combine(end_exclusive, time.min, tzinfo=UTC),
        )
    )
    for transaction in transactions:
        month = _month_start(_transaction_date(transaction.date))
        key = (transaction.category_id, month)
        totals[key] = totals.get(key, 0) - transaction.amount_cents
    return totals


def build_budget_month_report(
    session: Session, workspace_id: int, period_month: date
) -> BudgetMonthReport:
    """Build suggestions and accepted-budget status without writing any row."""
    if period_month.day != 1:
        raise PlanningValidationError({"period_month": "Budget month must start on day one."})
    source_start = _shift_month(period_month, -3)
    source_end = _month_end(_shift_month(period_month, -1))
    next_month = _shift_month(period_month, 1)
    accessible = _accessible_expense_categories(session, workspace_id)
    spending = _spending_by_category_and_month(
        session, workspace_id, source_start, next_month, accessible
    )
    budgets = {
        budget.category_id: budget
        for budget in session.scalars(
            select(Budget).where(
                Budget.workspace_id == workspace_id,
                Budget.period_month == period_month,
            )
        )
        if budget.category_id in accessible
    }
    historical_ids = {
        category_id
        for category_id, month in spending
        if month < period_month and spending[(category_id, month)] > 0
    }
    category_ids = historical_ids | set(budgets)
    source_months = tuple(_shift_month(period_month, offset) for offset in (-3, -2, -1))
    lines: list[BudgetLine] = []
    for category_id in category_ids:
        category = accessible[category_id]
        monthly = tuple(spending.get((category_id, month), 0) for month in source_months)
        median = sorted(monthly)[1]
        suggestion = BudgetSuggestion(
            monthly_spend_cents=monthly,
            median_cents=median,
            suggested_cents=(median * 110 + 50) // 100,
        )
        budget = budgets.get(category_id)
        current_spending = spending.get((category_id, period_month), 0)
        limit = budget.amount_cents if budget is not None else None
        lines.append(
            BudgetLine(
                category_id=category_id,
                category_name=category.name,
                budget_id=budget.id if budget is not None else None,
                limit_cents=limit,
                spent_cents=current_spending,
                remaining_cents=limit - current_spending if limit is not None else None,
                suggestion=suggestion,
            )
        )
    lines.sort(key=lambda line: (line.category_name.casefold(), line.category_id))
    return BudgetMonthReport(period_month, source_start, source_end, tuple(lines))


def save_budget(
    session: Session,
    workspace_id: int,
    category_id: int,
    period_month: date,
    amount_cents: int,
) -> Budget:
    """Create or update one explicitly accepted workspace category budget."""
    errors: dict[str, str] = {}
    if period_month.day != 1:
        errors["period_month"] = "Budget month must start on day one."
    if amount_cents < 0:
        errors["amount"] = "Amount must be zero or more."
    elif amount_cents > MAX_MONEY_CENTS:
        errors["amount"] = "Amount is too large."
    if errors:
        raise PlanningValidationError(errors)
    category = session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    if category is None:
        raise PlanningNotFoundError
    if category.kind != "expense" or category.name.strip().casefold() == "uncategorized":
        raise PlanningValidationError({"category_id": "Choose an expense category."})
    budget = session.scalar(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.category_id == category_id,
            Budget.period_month == period_month,
        )
    )
    if budget is None:
        budget = Budget(
            workspace_id=workspace_id,
            category_id=category_id,
            period_month=period_month,
            amount_cents=amount_cents,
        )
        session.add(budget)
    else:
        budget.amount_cents = amount_cents
    session.flush()
    return budget


def _inclusive_month_count(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month + 1


def project_goal(goal: SavingsGoal, as_of_date: date) -> GoalProjection:
    """Calculate the one missing goal planning value from integer cents."""
    remaining = max(goal.target_amount_cents - goal.current_amount_cents, 0)
    if remaining == 0:
        return GoalProjection(
            goal_id=goal.id,
            name=goal.name,
            target_amount_cents=goal.target_amount_cents,
            current_amount_cents=goal.current_amount_cents,
            remaining_cents=0,
            supplied_target_date=goal.target_date,
            supplied_monthly_contribution_cents=goal.monthly_contribution_cents,
            calculated_target_date=as_of_date if goal.target_date is None else None,
            calculated_monthly_contribution_cents=0 if goal.target_date is not None else None,
            contribution_months=0,
            status="completed",
        )
    if goal.target_date is not None:
        if goal.target_date < as_of_date:
            months = None
            calculated_contribution = None
            status = "off_track"
        else:
            months = _inclusive_month_count(as_of_date, goal.target_date)
            calculated_contribution = (remaining + months - 1) // months
            status = "on_track"
        calculated_target = None
    else:
        contribution = goal.monthly_contribution_cents
        if contribution is None or contribution <= 0:
            raise PlanningValidationError(
                {"monthly_contribution": "Monthly contribution must be greater than zero."}
            )
        months = (remaining + contribution - 1) // contribution
        final_month = _shift_month(as_of_date, months - 1)
        calculated_target = _month_end(final_month)
        calculated_contribution = None
        status = "on_track"
    return GoalProjection(
        goal_id=goal.id,
        name=goal.name,
        target_amount_cents=goal.target_amount_cents,
        current_amount_cents=goal.current_amount_cents,
        remaining_cents=remaining,
        supplied_target_date=goal.target_date,
        supplied_monthly_contribution_cents=goal.monthly_contribution_cents,
        calculated_target_date=calculated_target,
        calculated_monthly_contribution_cents=calculated_contribution,
        contribution_months=months,
        status=status,
    )


def _validated_goal_input(
    values: GoalInput, as_of_date: date, *, reject_past_deadline: bool
) -> GoalInput:
    name = " ".join(values.name.split())
    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Goal name is required."
    elif len(name) > 255:
        errors["name"] = "Goal name must be 255 characters or fewer."
    if values.target_amount_cents <= 0:
        errors["target_amount"] = "Target amount must be greater than zero."
    elif values.target_amount_cents > MAX_MONEY_CENTS:
        errors["target_amount"] = "Target amount is too large."
    if values.current_amount_cents < 0:
        errors["current_amount"] = "Current savings must be zero or more."
    elif values.current_amount_cents > MAX_MONEY_CENTS:
        errors["current_amount"] = "Current savings is too large."
    supplied_count = int(values.target_date is not None) + int(
        values.monthly_contribution_cents is not None
    )
    if supplied_count != 1:
        message = "Enter exactly one of target date or monthly contribution."
        errors["target_date"] = message
        errors["monthly_contribution"] = message
    if values.monthly_contribution_cents is not None and values.monthly_contribution_cents <= 0:
        errors["monthly_contribution"] = "Monthly contribution must be greater than zero."
    elif (
        values.monthly_contribution_cents is not None
        and values.monthly_contribution_cents > MAX_MONEY_CENTS
    ):
        errors["monthly_contribution"] = "Monthly contribution is too large."
    if (
        reject_past_deadline
        and values.target_date is not None
        and values.target_date < as_of_date
        and values.current_amount_cents < values.target_amount_cents
    ):
        errors["target_date"] = "Target date cannot be in the past for an unfinished goal."
    if errors:
        raise PlanningValidationError(errors)
    return GoalInput(
        name=name,
        target_amount_cents=values.target_amount_cents,
        current_amount_cents=values.current_amount_cents,
        target_date=values.target_date,
        monthly_contribution_cents=values.monthly_contribution_cents,
    )


def create_goal(
    session: Session,
    workspace_id: int,
    values: GoalInput,
    as_of_date: date,
) -> SavingsGoal:
    """Validate and create one workspace savings goal."""
    cleaned = _validated_goal_input(values, as_of_date, reject_past_deadline=True)
    goal = SavingsGoal(
        workspace_id=workspace_id,
        name=cleaned.name,
        target_amount_cents=cleaned.target_amount_cents,
        current_amount_cents=cleaned.current_amount_cents,
        target_date=cleaned.target_date,
        monthly_contribution_cents=cleaned.monthly_contribution_cents,
    )
    session.add(goal)
    session.flush()
    return goal


def get_workspace_goal(session: Session, workspace_id: int, goal_id: int) -> SavingsGoal:
    """Load one goal without revealing whether a foreign ID exists."""
    goal = session.scalar(
        select(SavingsGoal).where(
            SavingsGoal.id == goal_id,
            SavingsGoal.workspace_id == workspace_id,
        )
    )
    if goal is None:
        raise GoalNotFoundError
    return goal


def update_goal(
    session: Session,
    workspace_id: int,
    goal_id: int,
    values: GoalInput,
    as_of_date: date,
) -> SavingsGoal:
    """Update one authorized goal while retaining overdue plans truthfully."""
    goal = get_workspace_goal(session, workspace_id, goal_id)
    cleaned = _validated_goal_input(values, as_of_date, reject_past_deadline=False)
    goal.name = cleaned.name
    goal.target_amount_cents = cleaned.target_amount_cents
    goal.current_amount_cents = cleaned.current_amount_cents
    goal.target_date = cleaned.target_date
    goal.monthly_contribution_cents = cleaned.monthly_contribution_cents
    session.flush()
    return goal


def list_goal_projections(
    session: Session, workspace_id: int, as_of_date: date
) -> tuple[GoalProjection, ...]:
    """Return deterministic projections for only the active workspace."""
    goals = session.scalars(
        select(SavingsGoal).where(SavingsGoal.workspace_id == workspace_id).order_by(SavingsGoal.id)
    )
    return tuple(project_goal(goal, as_of_date) for goal in goals)
