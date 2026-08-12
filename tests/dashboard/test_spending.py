from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.dashboard.service import (
    SpendingPeriodValidationError,
    build_spending_report,
    resolve_spending_period,
)
from app.db.models import Category, Transaction, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("key", "start", "end"),
    [
        ("last_6_months", date(2026, 2, 13), date(2026, 8, 12)),
        ("year_to_date", date(2026, 1, 1), date(2026, 8, 12)),
        ("last_1_year", date(2025, 8, 13), date(2026, 8, 12)),
        ("last_3_years", date(2023, 8, 13), date(2026, 8, 12)),
        ("last_5_years", date(2021, 8, 13), date(2026, 8, 12)),
    ],
)
def test_resolve_spending_period_uses_inclusive_deterministic_windows(
    key: str, start: date, end: date
) -> None:
    period = resolve_spending_period(key, "", date(2026, 8, 12))

    assert period.start_date == start
    assert period.end_date == end
    assert period.selected_month == "2026-08"


def test_calendar_month_is_selectable_and_capped_by_current_as_of_day() -> None:
    complete = resolve_spending_period("month", "2026-02", date(2026, 8, 12))
    current = resolve_spending_period("month", "2026-08", date(2026, 8, 12))

    assert (complete.start_date, complete.end_date) == (date(2026, 2, 1), date(2026, 2, 28))
    assert (current.start_date, current.end_date) == (date(2026, 8, 1), date(2026, 8, 12))

    with pytest.raises(SpendingPeriodValidationError):
        resolve_spending_period("month", "2026-09", date(2026, 8, 12))
    with pytest.raises(SpendingPeriodValidationError):
        resolve_spending_period("quarter", "2026-08", date(2026, 8, 12))


def test_default_month_is_zero_padded_for_year_one_on_every_platform() -> None:
    period = resolve_spending_period("month", "", date.min)

    assert period.selected_month == "0001-01"
    assert (period.start_date, period.end_date) == (date.min, date.min)


def _transaction(
    session,
    workspace_id: int,
    *,
    day: int,
    amount: int,
    category: Category | None,
    merchant: str,
) -> None:
    session.add(
        Transaction(
            workspace_id=workspace_id,
            date=datetime(2026, 8, day, 12, tzinfo=UTC),
            description=merchant,
            normalized_merchant=merchant,
            amount_cents=amount,
            category_id=category.id if category else None,
            categorization_source="test",
        )
    )


def test_spending_report_groups_expenses_and_excludes_income_transfers_and_other_workspaces(
    session, workspace, other_workspace
) -> None:
    groceries = Category(name="Groceries", kind="expense")
    dining = Category(name="Dining", kind="expense")
    uncategorized = Category(name="Uncategorized", kind="expense")
    income = Category(name="Income", kind="income")
    transfer = Category(name="Transfers", kind="transfer")
    session.add_all((groceries, dining, uncategorized, income, transfer))
    session.flush()

    _transaction(session, workspace.id, day=1, amount=-1_000, category=groceries, merchant="Market")
    _transaction(session, workspace.id, day=2, amount=-500, category=dining, merchant="Market")
    _transaction(
        session, workspace.id, day=3, amount=-900, category=uncategorized, merchant="Unknown"
    )
    _transaction(session, workspace.id, day=4, amount=-700, category=income, merchant="Correction")
    _transaction(session, workspace.id, day=5, amount=-800, category=transfer, merchant="Transfer")
    _transaction(session, workspace.id, day=6, amount=50_000, category=groceries, merchant="Refund")
    _transaction(
        session,
        other_workspace.id,
        day=2,
        amount=-999_999,
        category=groceries,
        merchant="PRIVATE MERCHANT",
    )
    session.commit()

    period = resolve_spending_period("month", "2026-08", date(2026, 8, 31))
    report = build_spending_report(session, workspace.id, period)

    assert report.total_cents == 1_500
    assert report.transaction_count == 2
    assert report.needs_review_count == 2
    assert [
        (line.label, line.spending_cents, line.percentage_basis_points)
        for line in report.categories
    ] == [("Groceries", 1_000, 6_667), ("Dining", 500, 3_333)]
    assert [
        (line.label, line.spending_cents, line.transaction_count) for line in report.merchants
    ] == [("Market", 1_500, 2)]
    assert "PRIVATE MERCHANT" not in repr(report)

    category_query = parse_qs(urlparse(report.categories[0].transactions_url).query)
    assert category_query == {
        "start_date": ["2026-08-01"],
        "end_date": ["2026-08-31"],
        "direction": ["expense"],
        "spending": ["only"],
        "category_id": [str(groceries.id)],
    }
    assert parse_qs(urlparse(report.review_transactions_url).query)["review"] == ["needed"]


def test_spending_report_order_is_stable_for_equal_totals(session, workspace) -> None:
    alpha = Category(name="alpha", kind="expense")
    beta = Category(name="Beta", kind="expense")
    session.add_all((beta, alpha))
    session.flush()
    _transaction(session, workspace.id, day=2, amount=-100, category=beta, merchant="Zulu")
    _transaction(session, workspace.id, day=1, amount=-100, category=alpha, merchant="alpha")
    session.commit()

    report = build_spending_report(
        session,
        workspace.id,
        resolve_spending_period("month", "2026-08", date(2026, 8, 31)),
    )

    assert [line.label for line in report.categories] == ["alpha", "Beta"]
    assert [line.label for line in report.merchants] == ["alpha", "Zulu"]


@pytest.mark.anyio
async def test_dashboard_renders_period_controls_breakdowns_and_exact_drilldowns(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                groceries = Category(workspace_id=workspace_id, name="Food shops", kind="expense")
                transfer = Category(
                    workspace_id=workspace_id, name="Internal Transfer", kind="transfer"
                )
                session.add_all((groceries, transfer))
                uncategorized = session.scalar(
                    select(Category).where(Category.name == "Uncategorized")
                )
                assert uncategorized is not None
                session.flush()
                _transaction(
                    session,
                    workspace_id,
                    day=3,
                    amount=-12_345,
                    category=groceries,
                    merchant="Corner Market",
                )
                _transaction(
                    session,
                    workspace_id,
                    day=4,
                    amount=-90_000,
                    category=transfer,
                    merchant="Internal move",
                )
                _transaction(
                    session,
                    workspace_id,
                    day=5,
                    amount=-100,
                    category=uncategorized,
                    merchant="Unknown",
                )
                session.commit()

            response = await client.get(
                f"/workspaces/{workspace_id}/dashboard",
                params={
                    "as_of": "2026-08-12",
                    "spending_period": "month",
                    "spending_month": "2026-08",
                },
            )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "Where am I spending?" in response.text
    for option in (
        "Calendar month",
        "Last 6 months",
        "Year to date",
        "Rolling last 1 year",
        "Last 3 years",
        "Last 5 years",
    ):
        assert option in response.text
    assert "$123.45" in response.text
    assert "100.0%" in response.text
    assert "Spending by category" in response.text
    assert "Spending by merchant" in response.text
    assert "Corner Market" in response.text
    assert "Internal move" not in response.text
    assert "1 outgoing transaction needs category review" in response.text
    assert "spending=only" in response.text
    assert "merchant=Corner+Market" in response.text
    assert "review=needed" in response.text
    assert 'id="spending-category-chart"' in response.text
    assert 'id="spending-merchant-chart"' in response.text


@pytest.mark.anyio
async def test_dashboard_spending_period_validation_and_empty_review_state(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                uncategorized = session.scalar(
                    select(Category).where(Category.name == "Uncategorized")
                )
                assert uncategorized is not None
                _transaction(
                    session,
                    workspace_id,
                    day=5,
                    amount=-100,
                    category=uncategorized,
                    merchant="Unknown",
                )
                session.commit()
            review_only = await client.get(
                f"/workspaces/{workspace_id}/dashboard",
                params={"as_of": "2026-08-12"},
            )
            invalid = await client.get(
                f"/workspaces/{workspace_id}/dashboard",
                params={"as_of": "2026-08-12", "spending_period": "quarter"},
            )
    finally:
        engine.dispose()

    assert review_only.status_code == 200
    assert "No reviewed spending in this period" in review_only.text
    assert invalid.status_code == 422
    assert "Choose an available spending period." in invalid.text
