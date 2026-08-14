"""Presentation-safe formatting and aggregate chart data for dashboard reports."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.dashboard.types import AnnualCashFlow, DashboardReport, MonthlyCashFlow, SpendingReport


@dataclass(frozen=True)
class DashboardPageData:
    """Precomputed dashboard display state for declarative templates."""

    is_empty: bool
    has_accounts: bool
    has_transactions: bool
    has_known_position: bool
    has_position_history: bool
    savings_rate_basis_points: int | None
    needs_review_count: int


def format_money(cents: int) -> str:
    """Format integer cents with the minus sign before the dollar symbol."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def format_basis_points(basis_points: int) -> str:
    """Format integer basis points as a one-decimal percentage."""
    percentage = (Decimal(basis_points) / Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{percentage}%"


def dashboard_page_data(report: DashboardReport) -> DashboardPageData:
    """Derive bounded display-state facts without putting report logic in Jinja."""
    current_cash_flow = report.cash_flow_series[-1] if report.cash_flow_series else None
    return DashboardPageData(
        is_empty=(
            report.as_of_date is None
            and not report.position.accounts
            and not report.has_transactions
        ),
        has_accounts=bool(report.position.accounts),
        has_transactions=report.has_transactions,
        has_known_position=any(
            account.balance_cents is not None for account in report.position.accounts
        ),
        has_position_history=sum(
            point.net_worth_cents is not None for point in report.net_worth_series
        )
        >= 2,
        savings_rate_basis_points=(
            current_cash_flow.savings_rate_basis_points if current_cash_flow is not None else None
        ),
        needs_review_count=sum(point.needs_review_count for point in report.cash_flow_series),
    )


def chart_payload(
    report: DashboardReport,
    cash_flow_series: tuple[AnnualCashFlow | MonthlyCashFlow, ...] | None = None,
) -> dict[str, dict[str, list[str | int | None]]]:
    """Return the bounded aggregate values safe to serialize for local charts."""
    selected_cash_flow = cash_flow_series or report.cash_flow_series
    return {
        "net_worth": {
            "labels": [str(point.year) for point in report.net_worth_series],
            "values": [point.net_worth_cents for point in report.net_worth_series],
        },
        "cash_flow": {
            "labels": [
                point.label if isinstance(point, MonthlyCashFlow) else str(point.year)
                for point in selected_cash_flow
            ],
            "income": [point.income_cents for point in selected_cash_flow],
            "spending": [point.spending_cents for point in selected_cash_flow],
        },
    }


def spending_chart_payload(
    report: SpendingReport,
) -> dict[str, dict[str, list[str | int]]]:
    """Return exact server-calculated spending breakdowns for local charts."""
    return {
        "categories": {
            "labels": [item.label for item in report.categories],
            "values": [item.spending_cents for item in report.categories],
        },
        "merchants": {
            "labels": [item.label for item in report.merchants],
            "values": [item.spending_cents for item in report.merchants],
        },
    }
