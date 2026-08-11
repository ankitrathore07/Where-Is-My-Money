from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.dashboard.service import (
    build_net_worth_series,
    get_position_summary,
    resolve_as_of_date,
)
from app.dashboard.types import AnnualPosition
from app.db.models import Account, AccountBalanceSnapshot, Transaction, Workspace


def _account(
    session: Session,
    workspace_id: int,
    name: str,
    account_type: str,
    is_liability: bool,
) -> Account:
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type=account_type,
        institution="Example Bank",
        is_liability=is_liability,
    )
    session.add(account)
    session.flush()
    return account


def _snapshot(
    session: Session,
    workspace_id: int,
    account_id: int,
    balance_cents: int,
    as_of_date: date,
) -> AccountBalanceSnapshot:
    snapshot = AccountBalanceSnapshot(
        workspace_id=workspace_id,
        account_id=account_id,
        balance_cents=balance_cents,
        as_of_date=as_of_date,
        source="manual",
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _transaction(session: Session, workspace_id: int, occurred_at: datetime) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=occurred_at,
        description="Synthetic transaction",
        amount_cents=-100,
        categorization_source="uncategorized",
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_position_uses_latest_eligible_snapshot_and_subtracts_liabilities(
    session: Session, workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    mortgage = _account(session, workspace.id, "Mortgage", "mortgage", True)
    missing = _account(session, workspace.id, "Brokerage", "investment_brokerage", False)
    _snapshot(session, workspace.id, checking.id, 800_000, date(2026, 7, 31))
    _snapshot(session, workspace.id, checking.id, 900_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, checking.id, 950_000, date(2026, 8, 11))
    _snapshot(session, workspace.id, mortgage.id, 8_300_000, date(2026, 8, 10))
    session.commit()

    summary = get_position_summary(session, workspace.id, date(2026, 8, 10))

    assert summary.assets_cents == 900_000
    assert summary.liabilities_cents == 8_300_000
    assert summary.net_worth_cents == -7_400_000
    assert summary.cash_cents == 900_000
    assert summary.missing_balance_count == 1
    assert [item.account_id for item in summary.accounts] == [checking.id, missing.id, mortgage.id]


def test_position_uses_larger_snapshot_id_for_same_day_corrections(
    session: Session, workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    first = _snapshot(session, workspace.id, checking.id, 100_000, date(2026, 8, 10))
    second = _snapshot(session, workspace.id, checking.id, 120_000, date(2026, 8, 10))

    summary = get_position_summary(session, workspace.id, date(2026, 8, 10))

    assert second.id > first.id
    assert summary.accounts[0].balance_cents == 120_000
    assert summary.accounts[0].as_of_date == date(2026, 8, 10)


def test_position_ignores_foreign_account_and_foreign_workspace_snapshots(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    foreign = _account(session, other_workspace.id, "Foreign checking", "checking", False)
    _snapshot(session, workspace.id, checking.id, 900_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, foreign.id, 99_999_999, date(2026, 8, 10))
    _snapshot(session, other_workspace.id, checking.id, 88_888_888, date(2026, 8, 10))

    summary = get_position_summary(session, workspace.id, date(2026, 8, 10))

    assert summary.assets_cents == 900_000
    assert [item.account_id for item in summary.accounts] == [checking.id]


def test_resolve_as_of_date_uses_latest_workspace_transaction_or_snapshot(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    foreign = _account(session, other_workspace.id, "Foreign", "checking", False)
    _transaction(session, workspace.id, datetime(2026, 8, 9, 23, 59, tzinfo=UTC))
    _snapshot(session, workspace.id, checking.id, 100_000, date(2026, 8, 10))
    _transaction(session, other_workspace.id, datetime(2099, 1, 1, tzinfo=UTC))
    _snapshot(session, other_workspace.id, foreign.id, 999_999, date(2099, 1, 1))

    assert resolve_as_of_date(session, workspace.id) == date(2026, 8, 10)
    assert resolve_as_of_date(session, other_workspace.id) == date(2099, 1, 1)


def test_resolve_as_of_date_returns_none_without_workspace_data(
    session: Session, workspace: Workspace
) -> None:
    assert resolve_as_of_date(session, workspace.id) is None


def test_net_worth_series_carries_forward_and_excludes_future_current_year_snapshot(
    session: Session, workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    mortgage = _account(session, workspace.id, "Mortgage", "mortgage", True)
    _snapshot(session, workspace.id, checking.id, 100_000, date(2021, 12, 31))
    _snapshot(session, workspace.id, mortgage.id, 50_000, date(2021, 12, 31))
    _snapshot(session, workspace.id, checking.id, 120_000, date(2022, 12, 31))
    _snapshot(session, workspace.id, checking.id, 130_000, date(2024, 12, 31))
    _snapshot(session, workspace.id, checking.id, 150_000, date(2025, 12, 31))
    _snapshot(session, workspace.id, mortgage.id, 60_000, date(2025, 12, 31))
    _snapshot(session, workspace.id, checking.id, 200_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, mortgage.id, 80_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, checking.id, 999_000, date(2026, 9, 1))

    series = build_net_worth_series(session, workspace.id, date(2026, 8, 10), years=5)

    assert [point.year for point in series] == [2022, 2023, 2024, 2025, 2026]
    assert series[0] == AnnualPosition(2022, 120_000, 50_000, 70_000)
    assert series[1] == AnnualPosition(2023, 120_000, 50_000, 70_000)
    assert series[-1] == AnnualPosition(2026, 200_000, 80_000, 120_000)


def test_net_worth_series_uses_none_for_years_before_any_balance(
    session: Session, workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    _snapshot(session, workspace.id, checking.id, 250_000, date(2024, 6, 1))

    series = build_net_worth_series(session, workspace.id, date(2026, 8, 10), years=5)

    assert series[:2] == (
        AnnualPosition(2022, None, None, None),
        AnnualPosition(2023, None, None, None),
    )
    assert series[2] == AnnualPosition(2024, 250_000, 0, 250_000)
