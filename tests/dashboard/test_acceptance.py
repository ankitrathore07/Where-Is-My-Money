from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.dashboard.demo import seed_dashboard_demo
from app.dashboard.presentation import chart_payload
from app.dashboard.service import build_dashboard_report
from app.db.models import Account, AccountBalanceSnapshot, Category, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_signed_in_user_can_view_the_fixed_demo_without_cross_workspace_data(
    tmp_path: Path,
) -> None:
    """Dropping membership checks or aggregate isolation would reveal a foreign workspace's data."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                session.add_all(
                    Category(workspace_id=None, name=name, kind=kind)
                    for name, kind in BUILTIN_CATEGORY_DEFINITIONS
                    if name != "Uncategorized"
                )
                user = session.scalar(select(User).where(User.email == "import-route@example.com"))
                assert user is not None
                workspace = seed_dashboard_demo(session, user)
                foreign_user = User(
                    google_sub="demo-foreign-user",
                    email="demo-foreign@example.com",
                    display_name="Foreign Demo User",
                )
                foreign_workspace = Workspace(
                    name="Foreign Dashboard", is_personal=True, owner=foreign_user
                )
                session.add(foreign_workspace)
                session.flush()
                foreign_account = Account(
                    workspace_id=foreign_workspace.id,
                    name="RAW FOREIGN FIXTURE VALUE",
                    account_type="checking",
                    is_liability=False,
                )
                session.add(foreign_account)
                session.flush()
                session.add(
                    AccountBalanceSnapshot(
                        workspace_id=foreign_workspace.id,
                        account_id=foreign_account.id,
                        balance_cents=99_999_999,
                        as_of_date=date(2026, 8, 10),
                        source="manual",
                    )
                )
                session.commit()
                demo_workspace_id = workspace.id
                foreign_workspace_id = foreign_workspace.id

            response = await client.get(
                f"/workspaces/{demo_workspace_id}/dashboard?as_of=2026-08-10"
            )
            accounts = await client.get(f"/workspaces/{demo_workspace_id}/accounts")
            foreign_accounts = await client.get(
                f"/workspaces/{foreign_workspace_id}/accounts", follow_redirects=False
            )
            foreign_dashboard = await client.get(
                f"/workspaces/{foreign_workspace_id}/dashboard", follow_redirects=False
            )
            with factory() as session:
                first = build_dashboard_report(session, demo_workspace_id, date(2026, 8, 10))
                second = build_dashboard_report(session, demo_workspace_id, date(2026, 8, 10))
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert accounts.status_code == 200
    assert "$367,750.00" in response.text
    assert "$83,130.00 owed" in response.text
    assert "$284,620.00" in response.text
    assert "$24,840.00" in response.text
    assert response.text.count("<td>2022</td>") == 2
    assert response.text.count("<td>2023</td>") == 2
    assert response.text.count("<td>2024</td>") == 2
    assert response.text.count("<td>2025</td>") == 2
    assert response.text.count("<td>2026</td>") == 2
    for name in (
        "Everyday Checking",
        "Emergency Savings",
        "Example 401(k)",
        "Example Brokerage",
        "Home Mortgage",
    ):
        assert name in accounts.text
        assert name in response.text
    assert "RAW FOREIGN FIXTURE VALUE" not in response.text
    assert "/static/vendor/chartjs/chart.umd.min.js" in response.text
    assert "/static/dashboard.js" in response.text
    assert "Net worth increased by $37,620.00 from 2025 to 2026." in response.text
    assert foreign_accounts.status_code == 404
    assert foreign_dashboard.status_code == 404
    assert first == second
    assert chart_payload(first) == chart_payload(second)
