from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dashboard.service import build_dashboard_report
from app.dashboard.types import DashboardReport
from app.db.models import Account, AccountBalanceSnapshot, Category, Transaction, Workspace


def _account(session: Session, workspace_id: int, name: str) -> Account:
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type="checking",
        institution="SECRET INSTITUTION",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    return account


def _snapshot(
    session: Session, workspace_id: int, account_id: int, amount: int, when: date
) -> None:
    session.add(
        AccountBalanceSnapshot(
            workspace_id=workspace_id,
            account_id=account_id,
            balance_cents=amount,
            as_of_date=when,
            source="manual",
        )
    )
    session.flush()


def _income(session: Session, workspace_id: int, amount: int, when: datetime) -> None:
    category = session.scalar(select(Category).where(Category.name == "Income"))
    if category is None:
        category = Category(name="Income", kind="income")
        session.add(category)
        session.flush()
    session.add(
        Transaction(
            workspace_id=workspace_id,
            date=when,
            description="SECRET DESCRIPTION",
            amount_cents=amount,
            category_id=category.id,
            categorization_source="test",
        )
    )
    session.flush()


def _expense(session: Session, workspace_id: int, amount: int, when: datetime) -> None:
    category = session.scalar(select(Category).where(Category.name == "Expense"))
    if category is None:
        category = Category(name="Expense", kind="expense")
        session.add(category)
        session.flush()
    session.add(
        Transaction(
            workspace_id=workspace_id,
            date=when,
            description="Synthetic expense",
            amount_cents=-amount,
            category_id=category.id,
            categorization_source="test",
        )
    )
    session.flush()


def test_report_is_frozen_repeatable_and_prioritizes_net_worth_then_savings_then_position(
    session: Session, workspace: Workspace
) -> None:
    account = _account(session, workspace.id, "Primary checking")
    _snapshot(session, workspace.id, account.id, 100_000, date(2025, 12, 31))
    _snapshot(session, workspace.id, account.id, 130_000, date(2026, 8, 10))
    _income(session, workspace.id, 500_000, datetime(2025, 1, 1, tzinfo=UTC))
    _income(session, workspace.id, 500_000, datetime(2026, 1, 1, tzinfo=UTC))

    first = build_dashboard_report(session, workspace.id, date(2026, 8, 10))
    second = build_dashboard_report(session, workspace.id, date(2026, 8, 10))

    assert isinstance(first, DashboardReport)
    assert first == second
    assert len(first.highlights) == 3
    assert first.highlights[0].kind == "net_worth_improved"
    assert first.highlights[0].detail == "Net worth increased by $300.00 from 2025 to 2026."
    assert first.highlights[1].kind == "savings"
    assert first.highlights[1].title == "Saved $5,000.00"
    assert "100.0%" in first.highlights[1].detail
    assert first.highlights[2].kind == "largest_position"
    assert "Primary checking" in first.highlights[2].detail


def test_report_uses_missing_balances_before_largest_position_and_lowest_id_for_a_tie(
    session: Session, workspace: Workspace
) -> None:
    first = _account(session, workspace.id, "First")
    second = _account(session, workspace.id, "Second")
    missing = _account(session, workspace.id, "Missing")
    _snapshot(session, workspace.id, first.id, 200_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, second.id, -200_000, date(2026, 8, 10))

    report = build_dashboard_report(session, workspace.id, date(2026, 8, 10))

    assert report.highlights[-1].kind == "missing_balances"
    assert report.highlights[-1].detail == "1 account balance needs to be added."
    session.add(
        AccountBalanceSnapshot(
            workspace_id=workspace.id,
            account_id=missing.id,
            balance_cents=1,
            as_of_date=date(2026, 8, 10),
            source="manual",
        )
    )
    session.flush()
    complete = build_dashboard_report(session, workspace.id, date(2026, 8, 10))
    assert complete.highlights[-1].kind == "largest_position"
    assert complete.highlights[-1].detail.startswith("First is the largest known position")


def test_report_without_workspace_data_is_truthful_and_uses_setup_highlight(
    session: Session, workspace: Workspace
) -> None:
    report = build_dashboard_report(session, workspace.id)

    assert report.as_of_date is None
    assert report.net_worth_series == ()
    assert report.cash_flow_series == ()
    assert report.position.accounts == ()
    assert report.highlights[0].kind == "setup"
    assert (
        report.highlights[0].detail
        == "Add an account, balance, or transaction to see your dashboard."
    )


def test_report_describes_negative_savings_as_a_deficit_with_negative_tone(
    session: Session, workspace: Workspace
) -> None:
    _income(session, workspace.id, 1_000, datetime(2025, 1, 1, tzinfo=UTC))
    _expense(session, workspace.id, 500, datetime(2025, 1, 1, tzinfo=UTC))
    _income(session, workspace.id, 1_000, datetime(2026, 1, 1, tzinfo=UTC))
    _expense(session, workspace.id, 1_500, datetime(2026, 1, 1, tzinfo=UTC))

    report = build_dashboard_report(session, workspace.id, date(2026, 8, 10))

    highlight = report.highlights[0]
    assert highlight.kind == "savings"
    assert highlight.title == "Savings deficit of $5.00"
    assert highlight.detail == (
        "Income minus spending was -$5.00, a -50.0% savings rate. That is -100.0% versus 2025."
    )
    assert highlight.tone == "negative"


def test_report_describes_zero_savings_neutrally(session: Session, workspace: Workspace) -> None:
    _income(session, workspace.id, 1_000, datetime(2026, 1, 1, tzinfo=UTC))
    _expense(session, workspace.id, 1_000, datetime(2026, 1, 1, tzinfo=UTC))

    report = build_dashboard_report(session, workspace.id, date(2026, 8, 10))

    highlight = report.highlights[0]
    assert highlight.kind == "savings"
    assert highlight.title == "Income matched spending"
    assert highlight.detail == "Income minus spending was $0.00, a 0.0% savings rate."
    assert highlight.tone == "neutral"
