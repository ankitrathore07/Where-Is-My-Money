from datetime import date

from app.dashboard.presentation import (
    chart_payload,
    format_basis_points,
    format_money,
    spending_chart_payload,
)
from app.dashboard.types import (
    AnnualCashFlow,
    AnnualPosition,
    DashboardHighlight,
    DashboardReport,
    PositionSummary,
    SpendingBreakdown,
    SpendingPeriod,
    SpendingReport,
)


def _report() -> DashboardReport:
    return DashboardReport(
        as_of_date=date(2026, 8, 10),
        has_transactions=True,
        position=PositionSummary(0, 0, 0, 0, 0, ()),
        net_worth_series=(
            AnnualPosition(2025, None, None, None),
            AnnualPosition(2026, 100, 0, 100),
        ),
        cash_flow_series=(
            AnnualCashFlow(2025, None, None, None, None, 0),
            AnnualCashFlow(2026, 500, 300, 200, 4_000, 0),
        ),
        highlights=(DashboardHighlight("setup", "Setup", "SECRET DESCRIPTION", "neutral"),),
    )


def test_money_and_basis_point_formatting_preserve_sign_and_exact_tenths() -> None:
    assert format_money(0) == "$0.00"
    assert format_money(1_234) == "$12.34"
    assert format_money(-1_234) == "-$12.34"
    assert format_basis_points(2_480) == "24.8%"
    assert format_basis_points(-5) == "-0.1%"


def test_chart_payload_contains_only_chronological_aggregate_series() -> None:
    payload = chart_payload(_report())

    assert payload == {
        "net_worth": {"labels": ["2025", "2026"], "values": [None, 100]},
        "cash_flow": {
            "labels": ["2025", "2026"],
            "income": [None, 500],
            "spending": [None, 300],
        },
    }
    assert "SECRET INSTITUTION" not in str(payload)
    assert "SECRET DESCRIPTION" not in str(payload)


def test_spending_chart_payload_contains_only_breakdown_labels_and_cents() -> None:
    category = SpendingBreakdown("1", "Groceries", 12_345, 10_000, 2, "/support")
    merchant = SpendingBreakdown("Market", "Market", 12_345, 10_000, 2, "/merchant")
    report = SpendingReport(
        SpendingPeriod("month", "Calendar month", date(2026, 8, 1), date(2026, 8, 10), "2026-08"),
        12_345,
        2,
        1,
        (category,),
        (merchant,),
        "/all",
        "/review",
    )

    assert spending_chart_payload(report) == {
        "categories": {"labels": ["Groceries"], "values": [12_345]},
        "merchants": {"labels": ["Market"], "values": [12_345]},
    }
    assert "/support" not in str(spending_chart_payload(report))
