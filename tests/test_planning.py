from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Budget, Category, InsightSnapshot, SavingsGoal


def test_budget_roundtrip(session: Session, workspace) -> None:
    """A budget sets a monthly category limit in integer cents."""
    cat = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    session.add(cat)
    session.commit()

    budget = Budget(
        workspace_id=workspace.id,
        category_id=cat.id,
        amount_cents=50000,
        period_month=date(2026, 1, 1),
    )
    session.add(budget)
    session.commit()

    fetched = session.get(Budget, budget.id)
    assert fetched is not None
    assert fetched.amount_cents == 50000
    assert isinstance(fetched.amount_cents, int)
    assert fetched.category is not None
    assert fetched.category.name == "Groceries"


def test_budget_unique_per_category_per_month(session: Session, workspace) -> None:
    """Only one budget per (workspace, category, month)."""
    cat = Category(workspace_id=workspace.id, name="Dining", kind="expense")
    session.add(cat)
    session.commit()

    b1 = Budget(
        workspace_id=workspace.id,
        category_id=cat.id,
        amount_cents=30000,
        period_month=date(2026, 1, 1),
    )
    b2 = Budget(
        workspace_id=workspace.id,
        category_id=cat.id,
        amount_cents=35000,
        period_month=date(2026, 1, 1),
    )
    session.add_all([b1, b2])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_budget_different_months_allowed(session: Session, workspace) -> None:
    """The same category can have budgets for different months."""
    cat = Category(workspace_id=workspace.id, name="Dining", kind="expense")
    session.add(cat)
    session.commit()

    b1 = Budget(
        workspace_id=workspace.id,
        category_id=cat.id,
        amount_cents=30000,
        period_month=date(2026, 1, 1),
    )
    b2 = Budget(
        workspace_id=workspace.id,
        category_id=cat.id,
        amount_cents=32000,
        period_month=date(2026, 2, 1),
    )
    session.add_all([b1, b2])
    session.commit()
    assert b1.id is not None
    assert b2.id is not None


def test_savings_goal_roundtrip(session: Session, workspace) -> None:
    """A savings goal has a target, current amount, and a deadline or contribution."""
    goal = SavingsGoal(
        workspace_id=workspace.id,
        name="Emergency Fund",
        target_amount_cents=1000000,
        current_amount_cents=250000,
        target_date=date(2026, 12, 31),
    )
    session.add(goal)
    session.commit()

    fetched = session.get(SavingsGoal, goal.id)
    assert fetched is not None
    assert fetched.target_amount_cents == 1000000
    assert fetched.current_amount_cents == 250000
    assert fetched.target_date == date(2026, 12, 31)
    assert fetched.monthly_contribution_cents is None


def test_savings_goal_with_monthly_contribution(session: Session, workspace) -> None:
    """A goal can specify a monthly contribution instead of a target date."""
    goal = SavingsGoal(
        workspace_id=workspace.id,
        name="Vacation",
        target_amount_cents=300000,
        current_amount_cents=0,
        monthly_contribution_cents=25000,
    )
    session.add(goal)
    session.commit()

    fetched = session.get(SavingsGoal, goal.id)
    assert fetched is not None
    assert fetched.monthly_contribution_cents == 25000
    assert fetched.target_date is None


def test_insight_snapshot_roundtrip(session: Session, workspace) -> None:
    """An insight snapshot stores computed results for a period as JSON."""
    snapshot = InsightSnapshot(
        workspace_id=workspace.id,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        snapshot_data={
            "spending_by_category": {"Groceries": 42000, "Dining": 18000},
            "recurring_charges": [{"merchant": "Netflix", "amount_cents": 1599}],
            "income_vs_spending": {"income": 380000, "spending": 210000},
        },
    )
    session.add(snapshot)
    session.commit()

    fetched = session.get(InsightSnapshot, snapshot.id)
    assert fetched is not None
    assert fetched.period_start == date(2026, 1, 1)
    assert fetched.snapshot_data["spending_by_category"]["Groceries"] == 42000
    assert fetched.snapshot_data["recurring_charges"][0]["merchant"] == "Netflix"
