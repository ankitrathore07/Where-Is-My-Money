"""Workspace-scoped, deterministic financial position calculations."""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dashboard.types import AccountPosition, AnnualPosition, PositionSummary
from app.db.models import Account, AccountBalanceSnapshot, Transaction

_CASH_ACCOUNT_TYPES = {"checking", "savings"}


def _workspace_accounts(session: Session, workspace_id: int) -> tuple[Account, ...]:
    return tuple(
        session.scalars(
            select(Account)
            .where(Account.workspace_id == workspace_id)
            .order_by(Account.account_type, func.lower(Account.name), Account.id)
        )
    )


def _latest_snapshots(
    session: Session, workspace_id: int, accounts: tuple[Account, ...], cutoff: date
) -> dict[int, AccountBalanceSnapshot]:
    if not accounts:
        return {}

    snapshots = session.scalars(
        select(AccountBalanceSnapshot)
        .where(
            AccountBalanceSnapshot.workspace_id == workspace_id,
            AccountBalanceSnapshot.account_id.in_(account.id for account in accounts),
            AccountBalanceSnapshot.as_of_date <= cutoff,
        )
        .order_by(
            AccountBalanceSnapshot.account_id,
            AccountBalanceSnapshot.as_of_date.desc(),
            AccountBalanceSnapshot.id.desc(),
        )
    )
    latest: dict[int, AccountBalanceSnapshot] = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.account_id, snapshot)
    return latest


def _position_summary(session: Session, workspace_id: int, cutoff: date) -> PositionSummary:
    accounts = _workspace_accounts(session, workspace_id)
    snapshots = _latest_snapshots(session, workspace_id, accounts, cutoff)
    positions: list[AccountPosition] = []
    assets_cents = 0
    liabilities_cents = 0
    cash_cents = 0
    missing_balance_count = 0

    for account in accounts:
        snapshot = snapshots.get(account.id)
        balance_cents = snapshot.balance_cents if snapshot is not None else None
        if balance_cents is None:
            missing_balance_count += 1
        elif account.is_liability:
            liabilities_cents += balance_cents
        else:
            assets_cents += balance_cents
            if account.account_type in _CASH_ACCOUNT_TYPES:
                cash_cents += balance_cents
        positions.append(
            AccountPosition(
                account_id=account.id,
                name=account.name,
                account_type=account.account_type,
                institution=account.institution,
                is_liability=account.is_liability,
                balance_cents=balance_cents,
                as_of_date=snapshot.as_of_date if snapshot is not None else None,
            )
        )

    return PositionSummary(
        assets_cents=assets_cents,
        liabilities_cents=liabilities_cents,
        net_worth_cents=assets_cents - liabilities_cents,
        cash_cents=cash_cents,
        missing_balance_count=missing_balance_count,
        accounts=tuple(positions),
    )


def get_position_summary(session: Session, workspace_id: int, cutoff: date) -> PositionSummary:
    """Return balances eligible at ``cutoff`` for one workspace's accounts."""
    return _position_summary(session, workspace_id, cutoff)


def resolve_as_of_date(session: Session, workspace_id: int) -> date | None:
    """Return the latest transaction or eligible balance calendar date for a workspace."""
    latest_transaction = session.scalar(
        select(func.max(Transaction.date)).where(Transaction.workspace_id == workspace_id)
    )
    latest_snapshot = session.scalar(
        select(func.max(AccountBalanceSnapshot.as_of_date)).where(
            AccountBalanceSnapshot.workspace_id == workspace_id,
            AccountBalanceSnapshot.account_id.in_(
                select(Account.id).where(Account.workspace_id == workspace_id)
            ),
        )
    )
    transaction_date = _calendar_date(latest_transaction)
    return max(
        (value for value in (transaction_date, latest_snapshot) if value is not None), default=None
    )


def _calendar_date(value: datetime | date | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def build_net_worth_series(
    session: Session, workspace_id: int, cutoff: date, *, years: int = 5
) -> tuple[AnnualPosition, ...]:
    """Build ascending annual positions with historical December 31 cutoffs."""
    points: list[AnnualPosition] = []
    for year in range(cutoff.year - years + 1, cutoff.year + 1):
        annual_cutoff = cutoff if year == cutoff.year else date(year, 12, 31)
        summary = _position_summary(session, workspace_id, annual_cutoff)
        if all(account.balance_cents is None for account in summary.accounts):
            points.append(AnnualPosition(year, None, None, None))
        else:
            points.append(
                AnnualPosition(
                    year,
                    summary.assets_cents,
                    summary.liabilities_cents,
                    summary.net_worth_cents,
                )
            )
    return tuple(points)
