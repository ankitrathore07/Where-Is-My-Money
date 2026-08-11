from datetime import date

from app.dashboard.presentation import chart_payload, format_basis_points, format_money
from app.dashboard.types import (
    AnnualCashFlow,
    AnnualPosition,
    DashboardHighlight,
    DashboardReport,
    PositionSummary,
)


def _report() -> DashboardReport:
    return DashboardReport(
        as_of_date=date(2026, 8, 10),
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
