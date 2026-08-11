"""Presentation-safe formatting and aggregate chart data for dashboard reports."""

from decimal import ROUND_HALF_UP, Decimal

from app.dashboard.types import DashboardReport


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


def chart_payload(report: DashboardReport) -> dict[str, dict[str, list[str | int | None]]]:
    """Return the bounded aggregate values safe to serialize for local charts."""
    return {
        "net_worth": {
            "labels": [str(point.year) for point in report.net_worth_series],
            "values": [point.net_worth_cents for point in report.net_worth_series],
        },
        "cash_flow": {
            "labels": [str(point.year) for point in report.cash_flow_series],
            "income": [point.income_cents for point in report.cash_flow_series],
            "spending": [point.spending_cents for point in report.cash_flow_series],
        },
    }
