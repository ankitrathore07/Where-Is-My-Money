from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.analytics.service import (
    summarize_cash_flow,
    summarize_household_spending,
    summarize_recent_cash_flow,
)
from app.analytics.types import CashFlowSummary, HouseholdSpendingSummary
from app.db.models import Category, Tag, Transaction, Workspace


def _category(session: Session, name: str, kind: str) -> Category:
    category = Category(workspace_id=None, name=name, kind=kind)
    session.add(category)
    session.flush()
    return category


def _transaction(
    session: Session,
    workspace_id: int,
    occurred_on: date,
    amount_cents: int,
    category: Category | None,
    merchant: str,
    *,
    tags: tuple[Tag, ...] = (),
    billing_period_months: int | None = None,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(occurred_on.year, occurred_on.month, occurred_on.day, tzinfo=UTC),
        description=merchant,
        normalized_merchant=merchant,
        amount_cents=amount_cents,
        category=category,
        tags=list(tags),
        billing_period_months=billing_period_months,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_custom_range_cash_flow_returns_savings_without_transfers_or_foreign_rows(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    expense = _category(session, "Housing", "expense")
    transfer = _category(session, "Transfers", "transfer")
    _transaction(session, workspace.id, date(2026, 1, 1), 500_000, income, "Payroll")
    _transaction(session, workspace.id, date(2026, 1, 2), -300_000, expense, "Mortgage")
    _transaction(session, workspace.id, date(2026, 1, 3), -50_000, transfer, "Card Payment")
    _transaction(session, workspace.id, date(2026, 1, 4), -1_000, None, "Unknown")
    _transaction(session, other_workspace.id, date(2026, 1, 2), 9_999_999, income, "Other")

    summary = summarize_cash_flow(
        session,
        workspace.id,
        date(2026, 1, 1),
        date(2026, 1, 31),
    )

    assert summary == CashFlowSummary(
        date(2026, 1, 1),
        date(2026, 1, 31),
        500_000,
        300_000,
        200_000,
        4_000,
        1,
    )


def test_household_spending_uses_tags_and_billing_cadence_for_monthly_normalization(
    session: Session, workspace: Workspace
) -> None:
    housing = _category(session, "Housing", "expense")
    utilities = _category(session, "Utilities", "expense")
    insurance = _category(session, "Insurance", "expense")
    household = Tag(workspace_id=None, name="Household Expenditure")
    session.add(household)
    session.flush()
    for month in (1, 2, 3):
        _transaction(
            session,
            workspace.id,
            date(2026, month, 1),
            -200_000,
            housing,
            "Mortgage",
            billing_period_months=1,
        )
        _transaction(
            session,
            workspace.id,
            date(2026, month, 5),
            -30_000,
            utilities,
            "Utilities",
        )
    _transaction(
        session,
        workspace.id,
        date(2026, 1, 10),
        -120_000,
        insurance,
        "Home Insurance",
        tags=(household,),
        billing_period_months=12,
    )
    _transaction(
        session,
        workspace.id,
        date(2026, 2, 10),
        -5_000,
        None,
        "Needs review",
        tags=(household,),
    )

    summary = summarize_household_spending(
        session,
        workspace.id,
        date(2026, 1, 1),
        date(2026, 3, 31),
    )

    assert summary == HouseholdSpendingSummary(
        date(2026, 1, 1),
        date(2026, 3, 31),
        810_000,
        240_000,
        7,
        1,
    )


def test_analytics_rejects_reversed_custom_range(session: Session, workspace: Workspace) -> None:
    with pytest.raises(ValueError, match="start_date"):
        summarize_cash_flow(session, workspace.id, date(2026, 2, 1), date(2026, 1, 1))


def test_recent_cash_flow_supports_three_month_savings_window(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    _transaction(session, workspace.id, date(2026, 5, 10), 99_999, income, "Too old")
    _transaction(session, workspace.id, date(2026, 5, 11), 30_000, income, "Included")

    summary = summarize_recent_cash_flow(session, workspace.id, date(2026, 8, 10), 3)

    assert summary.start_date == date(2026, 5, 11)
    assert summary.end_date == date(2026, 8, 10)
    assert summary.savings_cents == 30_000
