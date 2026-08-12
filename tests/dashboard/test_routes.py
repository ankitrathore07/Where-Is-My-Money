import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Account, AccountBalanceSnapshot, Category, Transaction, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_dashboard_requires_authentication(tmp_path: Path) -> None:
    """Removing the current-user dependency would expose a workspace dashboard."""
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/dashboard", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_empty_dashboard_offers_account_and_transaction_setup(tmp_path: Path) -> None:
    """Removing the dashboard empty state would leave a new member without next actions."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.get(f"/workspaces/{workspace_id}/dashboard")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "No accounts or transactions yet." in response.text
    assert "Add your first account" in response.text
    assert "Import transactions" in response.text


@pytest.mark.anyio
async def test_account_without_financial_data_remains_visible_with_a_balance_action(
    tmp_path: Path,
) -> None:
    """Treating an account-only workspace as empty would hide the account that needs a balance."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = _account(session, workspace_id, "Checking", "checking", False)
                account_id = account.id
                session.commit()

            response = await client.get(f"/workspaces/{workspace_id}/dashboard")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "No accounts or transactions yet." not in response.text
    assert "Checking" in response.text
    assert "Balance not added" in response.text
    assert "Balance missing" in response.text
    assert "Add an account, balance, or transaction to see your dashboard." not in response.text
    assert '<h2 id="net-worth-heading">Unavailable</h2>' in response.text
    assert f'href="/workspaces/{workspace_id}/accounts/{account_id}/balances/new"' in response.text


def _account(
    session, workspace_id: int, name: str, account_type: str, is_liability: bool
) -> Account:
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type=account_type,
        institution="Example Financial",
        is_liability=is_liability,
    )
    session.add(account)
    session.flush()
    return account


def _snapshot(session, workspace_id: int, account_id: int, cents: int, as_of_date: date) -> None:
    session.add(
        AccountBalanceSnapshot(
            workspace_id=workspace_id,
            account_id=account_id,
            balance_cents=cents,
            as_of_date=as_of_date,
            source="manual",
        )
    )


class _FallbackTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[tuple[str, list[str]]] = []
        self._caption = ""
        self._capture: str | None = None
        self._current_row: list[str] | None = None
        self._current_cell = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "table":
            self._caption = ""
        elif tag == "caption":
            self._capture = "caption"
            self._current_cell = ""
        elif tag == "tr":
            self._current_row = []
        elif tag == "td" and self._current_row is not None:
            self._capture = "cell"
            self._current_cell = ""

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._current_cell += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption" and self._capture == "caption":
            self._caption = self._current_cell.strip()
            self._capture = None
        elif tag == "td" and self._capture == "cell" and self._current_row is not None:
            self._current_row.append(self._current_cell.strip())
            self._capture = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self.tables.append((self._caption, self._current_row))
            self._current_row = None


def _fallback_row(page: str, caption: str, year: str) -> list[str]:
    parser = _FallbackTableParser()
    parser.feed(page)
    return next(
        row for table_caption, row in parser.tables if table_caption == caption and row[0] == year
    )


def _chart_payload(page: str) -> dict[str, dict[str, list[str | int | None]]]:
    match = re.search(
        r'<script id="dashboard-chart-data" type="application/json">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


@pytest.mark.anyio
async def test_dashboard_renders_aggregate_totals_five_year_fallbacks_and_safe_payload(
    tmp_path: Path,
) -> None:
    """Replacing scoped aggregates with account or transaction detail would leak private data."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                checking = _account(session, workspace_id, "Checking", "checking", False)
                savings = _account(session, workspace_id, "Savings", "savings", False)
                brokerage = _account(
                    session, workspace_id, "Brokerage", "investment_brokerage", False
                )
                retirement = _account(session, workspace_id, "Retirement", "investment_401k", False)
                mortgage = _account(session, workspace_id, "Mortgage", "mortgage", True)
                for year in range(2022, 2027):
                    snapshot_date = date(year, 12, 31) if year < 2026 else date(2026, 8, 10)
                    _snapshot(session, workspace_id, checking.id, 842_000, snapshot_date)
                    _snapshot(session, workspace_id, savings.id, 1_642_000, snapshot_date)
                    _snapshot(session, workspace_id, brokerage.id, 11_660_000, snapshot_date)
                    _snapshot(session, workspace_id, retirement.id, 22_631_000, snapshot_date)
                    _snapshot(session, workspace_id, mortgage.id, 8_313_000, snapshot_date)
                income = Category(workspace_id=None, name="Salary", kind="income")
                expense = Category(workspace_id=None, name="Housing", kind="expense")
                session.add_all((income, expense))
                session.flush()
                for year in range(2022, 2027):
                    session.add_all(
                        (
                            Transaction(
                                workspace_id=workspace_id,
                                date=datetime(year, 6, 1, tzinfo=UTC),
                                description="Synthetic income",
                                amount_cents=200_000,
                                category_id=income.id,
                                categorization_source="test",
                            ),
                            Transaction(
                                workspace_id=workspace_id,
                                date=datetime(year, 6, 2, tzinfo=UTC),
                                description="Synthetic expense",
                                amount_cents=-50_000,
                                category_id=expense.id,
                                categorization_source="test",
                            ),
                        )
                    )
                foreign_user = User(
                    google_sub="dashboard-other-user", email="dashboard-other@example.com"
                )
                foreign_workspace = Workspace(
                    name="SECRET OTHER WORKSPACE", is_personal=True, owner=foreign_user
                )
                session.add(foreign_workspace)
                session.flush()
                foreign_account = _account(
                    session,
                    foreign_workspace.id,
                    "SECRET OTHER ACCOUNT",
                    "checking",
                    False,
                )
                _snapshot(
                    session, foreign_workspace.id, foreign_account.id, 99_999_999, date(2026, 8, 10)
                )
                session.add(
                    Transaction(
                        workspace_id=foreign_workspace.id,
                        date=datetime(2026, 8, 10, tzinfo=UTC),
                        description="SECRET OTHER TRANSACTION",
                        amount_cents=99_999,
                        categorization_source="test",
                    )
                )
                session.commit()
                foreign_workspace_id = foreign_workspace.id

            response = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")
            foreign_response = await client.get(
                f"/workspaces/{foreign_workspace_id}/dashboard", follow_redirects=False
            )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "$367,750.00" in response.text
    assert "$83,130.00 owed" in response.text
    assert "$284,620.00" in response.text
    assert "$24,840.00" in response.text
    assert (
        re.findall(r"<td>(202\d)</td>", response.text)
        == [
            "2022",
            "2023",
            "2024",
            "2025",
            "2026",
        ]
        * 2
    )
    assert "$2,000.00" in response.text
    assert "$500.00" in response.text
    assert "75.0%" in response.text
    assert 'href="/workspaces/' + str(workspace_id) + '/dashboard"' in response.text
    assert "SECRET OTHER" not in response.text
    assert foreign_response.status_code == 404
    match = re.search(
        r'<script id="dashboard-chart-data" type="application/json">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert set(payload) == {"cash_flow", "net_worth"}
    assert set(payload["net_worth"]) == {"labels", "values"}
    assert set(payload["cash_flow"]) == {"income", "labels", "spending"}


@pytest.mark.anyio
async def test_dashboard_rejects_bad_dates_and_keeps_partial_data_truthful(tmp_path: Path) -> None:
    """Dropping date validation or treating missing data as zero would misstate the position."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                mortgage = _account(session, workspace_id, "Mortgage", "mortgage", True)
                _account(session, workspace_id, "Savings", "savings", False)
                _snapshot(session, workspace_id, mortgage.id, 100_000, date(2026, 8, 10))
                expense = Category(workspace_id=None, name="Expense", kind="expense")
                session.add(expense)
                session.flush()
                session.add(
                    Transaction(
                        workspace_id=workspace_id,
                        date=datetime(2026, 8, 10, tzinfo=UTC),
                        description="Synthetic zero-income expense",
                        amount_cents=-5_000,
                        category_id=expense.id,
                        categorization_source="test",
                    )
                )
                session.commit()

            invalid = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-8-10")
            before_balance = await client.get(
                f"/workspaces/{workspace_id}/dashboard?as_of=2020-01-01"
            )
            current = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")
            future = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2099-01-01")
    finally:
        engine.dispose()

    assert invalid.status_code == 422
    assert "Use a valid date in YYYY-MM-DD format." in invalid.text
    assert before_balance.status_code == 200
    assert "Balance not added" in before_balance.text
    assert current.status_code == 200
    assert "-$1,000.00" in current.text
    assert "Unavailable" in current.text
    assert "1 account needs balances added" in current.text
    assert future.status_code == 200


@pytest.mark.anyio
async def test_dashboard_renders_inferred_and_explicit_date_min_account_history(
    tmp_path: Path,
) -> None:
    """A valid year-one account snapshot must not make the dashboard narrower than writes."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = _account(session, workspace_id, "Checking", "checking", False)
                _snapshot(session, workspace_id, account.id, 12_345, date.min)
                session.commit()

            inferred = await client.get(f"/workspaces/{workspace_id}/dashboard")
            explicit = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=0001-01-01")
    finally:
        engine.dispose()

    assert inferred.status_code == 200
    assert explicit.status_code == 200
    assert "As of 0001-01-01." in inferred.text
    assert "$123.45" in inferred.text
    assert _chart_payload(inferred.text)["net_worth"]["labels"] == ["1"]
    assert _chart_payload(inferred.text)["cash_flow"]["labels"] == ["1"]
    assert _chart_payload(explicit.text) == _chart_payload(inferred.text)


@pytest.mark.anyio
async def test_dashboard_renders_inferred_and_explicit_date_max_transaction(
    tmp_path: Path,
) -> None:
    """A transaction on date.max must remain visible through the full cutoff day."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                income = Category(workspace_id=None, name="Income", kind="income")
                session.add(income)
                session.flush()
                session.add(
                    Transaction(
                        workspace_id=workspace_id,
                        date=datetime.max.replace(tzinfo=UTC),
                        description="Synthetic boundary income",
                        amount_cents=12_345,
                        category_id=income.id,
                        categorization_source="test",
                    )
                )
                session.commit()

            inferred = await client.get(f"/workspaces/{workspace_id}/dashboard")
            explicit = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=9999-12-31")
    finally:
        engine.dispose()

    assert inferred.status_code == 200
    assert explicit.status_code == 200
    assert "As of 9999-12-31." in inferred.text
    assert "$123.45" in inferred.text
    assert _chart_payload(inferred.text)["cash_flow"] == {
        "labels": ["9995", "9996", "9997", "9998", "9999"],
        "income": [None, None, None, None, 12_345],
        "spending": [None, None, None, None, 0],
    }
    assert _chart_payload(explicit.text) == _chart_payload(inferred.text)


@pytest.mark.anyio
async def test_dashboard_rejects_malformed_and_nonexistent_iso_dates(tmp_path: Path) -> None:
    """Accepting the full ISO date domain must not weaken strict calendar parsing."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None

            malformed = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-8-10")
            nonexistent = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-02-30")
    finally:
        engine.dispose()

    assert malformed.status_code == 422
    assert nonexistent.status_code == 422
    assert "Use a valid date in YYYY-MM-DD format." in malformed.text
    assert "Use a valid date in YYYY-MM-DD format." in nonexistent.text


@pytest.mark.anyio
async def test_dashboard_surfaces_review_needed_transactions_with_existing_review_link(
    tmp_path: Path,
) -> None:
    """Removing the review aggregate would hide transactions excluded from dashboard rates."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = _account(session, workspace_id, "Checking", "checking", False)
                _snapshot(session, workspace_id, account.id, 1_000, date(2026, 8, 10))
                session.add(
                    Transaction(
                        workspace_id=workspace_id,
                        date=datetime(2025, 8, 10, tzinfo=UTC),
                        description="Needs a category",
                        amount_cents=-500,
                        categorization_source="test",
                    )
                )
                session.commit()
            response = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "1 transaction needs review" in response.text
    assert f'href="/workspaces/{workspace_id}/transactions"' in response.text


@pytest.mark.anyio
async def test_dashboard_distinguishes_missing_accounts_from_missing_transactions(
    tmp_path: Path,
) -> None:
    """Collapsing partial states into one empty message would give the wrong setup action."""
    transaction_application, transaction_factory, transaction_engine = build_route_test_app(
        tmp_path
    )
    account_application, account_factory, account_engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=transaction_application), base_url="http://testserver"
        ) as transaction_client:
            await complete_sign_in(transaction_client)
            with transaction_factory() as session:
                transaction_workspace_id = session.scalar(select(Workspace.id))
                assert transaction_workspace_id is not None
                income = Category(workspace_id=None, name="Salary", kind="income")
                session.add(income)
                session.flush()
                session.add(
                    Transaction(
                        workspace_id=transaction_workspace_id,
                        date=datetime(2026, 8, 10, tzinfo=UTC),
                        description="Synthetic income",
                        amount_cents=100_000,
                        category_id=income.id,
                        categorization_source="test",
                    )
                )
                session.commit()
            transaction_only = await transaction_client.get(
                f"/workspaces/{transaction_workspace_id}/dashboard?as_of=2026-08-10"
            )

        async with AsyncClient(
            transport=ASGITransport(app=account_application), base_url="http://testserver"
        ) as account_client:
            await complete_sign_in(account_client)
            with account_factory() as session:
                account_workspace_id = session.scalar(select(Workspace.id))
                assert account_workspace_id is not None
                account = _account(session, account_workspace_id, "Checking", "checking", False)
                _snapshot(session, account_workspace_id, account.id, 100_000, date(2026, 8, 10))
                session.commit()
            account_only = await account_client.get(
                f"/workspaces/{account_workspace_id}/dashboard?as_of=2026-08-10"
            )
    finally:
        transaction_engine.dispose()
        account_engine.dispose()

    assert transaction_only.status_code == 200
    assert "No accounts have been added yet." in transaction_only.text
    assert f'href="/workspaces/{transaction_workspace_id}/accounts/new"' in transaction_only.text
    assert "No transactions have been imported yet." not in transaction_only.text
    assert account_only.status_code == 200
    assert "No transactions have been imported yet." in account_only.text
    assert f'href="/workspaces/{account_workspace_id}/imports/new"' in account_only.text
    assert "No accounts have been added yet." not in account_only.text
    assert "No earlier account-balance history is available yet." in account_only.text


@pytest.mark.anyio
async def test_dashboard_treats_transfer_only_activity_as_transaction_history(
    tmp_path: Path,
) -> None:
    """Using cash-flow participation as presence would falsely prompt an active member to import."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                transfer = Category(workspace_id=None, name="Transfer", kind="transfer")
                session.add(transfer)
                session.flush()
                transfer_id = transfer.id
                session.add(
                    Transaction(
                        workspace_id=workspace_id,
                        date=datetime(2026, 8, 11, tzinfo=UTC),
                        description="Future transfer",
                        amount_cents=-10_000,
                        category_id=transfer_id,
                        categorization_source="test",
                    )
                )
                foreign_user = User(
                    google_sub="transfer-dashboard-foreign", email="transfer-foreign@example.com"
                )
                foreign_workspace = Workspace(
                    name="Transfer foreign workspace", is_personal=True, owner=foreign_user
                )
                session.add(foreign_workspace)
                session.flush()
                session.add(
                    Transaction(
                        workspace_id=foreign_workspace.id,
                        date=datetime(2026, 8, 10, tzinfo=UTC),
                        description="Foreign transfer",
                        amount_cents=-10_000,
                        category_id=transfer_id,
                        categorization_source="test",
                    )
                )
                session.commit()
            without_eligible_transfer = await client.get(
                f"/workspaces/{workspace_id}/dashboard?as_of=2026-08-10"
            )
            with factory() as session:
                session.add(
                    Transaction(
                        workspace_id=workspace_id,
                        date=datetime(2026, 8, 10, tzinfo=UTC),
                        description="Synthetic transfer",
                        amount_cents=-10_000,
                        category_id=transfer_id,
                        categorization_source="test",
                    )
                )
                session.commit()
            response = await client.get(f"/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert without_eligible_transfer.status_code == 200
    assert "No transactions have been imported yet." in without_eligible_transfer.text
    assert "No transactions have been imported yet." not in response.text
    assert _fallback_row(response.text, "Income and spending fallback", "2026") == [
        "2026",
        "No income data",
        "No spending data",
        "No savings data",
        "Unavailable",
    ]
    assert "needs review before it can be included in income and spending" not in response.text
