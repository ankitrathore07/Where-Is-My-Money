"""Workspace-scoped, deterministic financial dashboard calculations."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dashboard.types import (
    AccountPosition,
    AnnualCashFlow,
    AnnualPosition,
    DashboardHighlight,
    DashboardReport,
    PositionSummary,
)
from app.db.models import Account, AccountBalanceSnapshot, Category, Transaction

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


def build_cash_flow_series(
    session: Session, workspace_id: int, cutoff: date, *, years: int = 5
) -> tuple[AnnualCashFlow, ...]:
    """Build annual income, spending, and review totals through ``cutoff``."""
    start_year = cutoff.year - years + 1
    start_date = date(start_year, 1, 1)
    end_date = cutoff + timedelta(days=1)
    rows = session.execute(
        select(Transaction, Category)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.date >= datetime.combine(start_date, time.min, tzinfo=UTC),
            Transaction.date < datetime.combine(end_date, time.min, tzinfo=UTC),
        )
        .order_by(Transaction.date, Transaction.id)
    )
    totals = {
        year: {"income": 0, "spending": 0, "review": 0, "valid": False}
        for year in range(start_year, cutoff.year + 1)
    }
    for transaction, category in rows:
        transaction_date = _calendar_date(transaction.date)
        if transaction_date is None or transaction_date.year not in totals:
            continue
        total = totals[transaction_date.year]
        category_is_available = category is not None and (
            category.workspace_id is None or category.workspace_id == workspace_id
        )
        kind = category.kind if category_is_available else None
        if kind == "transfer":
            continue
        if kind == "income" and transaction.amount_cents > 0:
            total["income"] += transaction.amount_cents
            total["valid"] = True
        elif kind == "expense" and transaction.amount_cents < 0:
            total["spending"] -= transaction.amount_cents
            total["valid"] = True
        else:
            total["review"] += 1

    series: list[AnnualCashFlow] = []
    for year in range(start_year, cutoff.year + 1):
        total = totals[year]
        if not total["valid"]:
            series.append(AnnualCashFlow(year, None, None, None, None, total["review"]))
            continue
        income = total["income"]
        spending = total["spending"]
        savings = income - spending
        rate = None
        if income:
            rate = int(
                (Decimal(savings) / Decimal(income) * Decimal(10_000)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        series.append(
            AnnualCashFlow(year, income or None, spending, savings, rate, total["review"])
        )
    return tuple(series)


def build_dashboard_report(
    session: Session, workspace_id: int, as_of_date: date | None = None
) -> DashboardReport:
    """Return a repeatable dashboard report for one workspace."""
    cutoff = as_of_date if as_of_date is not None else resolve_as_of_date(session, workspace_id)
    if cutoff is None:
        position = _position_summary(session, workspace_id, date.min)
        return DashboardReport(
            as_of_date=None,
            position=position,
            net_worth_series=(),
            cash_flow_series=(),
            highlights=(_setup_highlight(),),
        )
    position = _position_summary(session, workspace_id, cutoff)
    net_worth_series = build_net_worth_series(session, workspace_id, cutoff)
    cash_flow_series = build_cash_flow_series(session, workspace_id, cutoff)
    return DashboardReport(
        as_of_date=cutoff,
        position=position,
        net_worth_series=net_worth_series,
        cash_flow_series=cash_flow_series,
        highlights=_build_highlights(position, net_worth_series, cash_flow_series),
    )


def _build_highlights(
    position: PositionSummary,
    net_worth_series: tuple[AnnualPosition, ...],
    cash_flow_series: tuple[AnnualCashFlow, ...],
) -> tuple[DashboardHighlight, ...]:
    highlights: list[DashboardHighlight] = []
    available_net_worth = [point for point in net_worth_series if point.net_worth_cents is not None]
    if len(available_net_worth) >= 2:
        previous, current = available_net_worth[-2:]
        delta = current.net_worth_cents - previous.net_worth_cents
        if delta > 0:
            kind, verb, tone = "net_worth_improved", "increased", "positive"
        elif delta < 0:
            kind, verb, tone = "net_worth_declined", "decreased", "negative"
        else:
            kind, verb, tone = "net_worth_unchanged", "was unchanged", "neutral"
        if delta:
            detail = (
                f"Net worth {verb} by {_format_money(abs(delta))} "
                f"from {previous.year} to {current.year}."
            )
        else:
            detail = f"Net worth {verb} from {previous.year} to {current.year}."
        highlights.append(DashboardHighlight(kind, "Net worth change", detail, tone))

    if cash_flow_series and cash_flow_series[-1].income_cents is not None:
        current = cash_flow_series[-1]
        detail = (
            f"You saved {_format_money(current.savings_cents or 0)} "
            f"at a {_format_basis_points(current.savings_rate_basis_points or 0)} savings rate."
        )
        if len(cash_flow_series) >= 2 and cash_flow_series[-2].income_cents is not None:
            prior = cash_flow_series[-2]
            change = (current.savings_rate_basis_points or 0) - (
                prior.savings_rate_basis_points or 0
            )
            detail += f" That is {_format_basis_points(change)} versus {prior.year}."
        highlights.append(
            DashboardHighlight(
                "savings", f"Saved {_format_money(current.savings_cents or 0)}", detail, "positive"
            )
        )

    if position.missing_balance_count:
        count = position.missing_balance_count
        noun = "account balance needs" if count == 1 else "account balances need"
        highlights.append(
            DashboardHighlight(
                "missing_balances",
                "Balance missing" if count == 1 else "Balances missing",
                f"{count} {noun} to be added.",
                "warning",
            )
        )
    else:
        known_positions = [
            account for account in position.accounts if account.balance_cents is not None
        ]
        if known_positions:
            largest = min(
                known_positions,
                key=lambda account: (-abs(account.balance_cents or 0), account.account_id),
            )
            highlights.append(
                DashboardHighlight(
                    "largest_position",
                    "Largest position",
                    (
                        f"{largest.name} is the largest known position "
                        f"at {_format_money(largest.balance_cents or 0)}."
                    ),
                    "neutral",
                )
            )
    return tuple(highlights[:3])


def _setup_highlight() -> DashboardHighlight:
    return DashboardHighlight(
        "setup",
        "Set up your dashboard",
        "Add an account, balance, or transaction to see your dashboard.",
        "neutral",
    )


def _format_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def _format_basis_points(basis_points: int) -> str:
    percentage = (Decimal(basis_points) / Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{percentage}%"
