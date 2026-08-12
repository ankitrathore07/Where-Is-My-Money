from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Account, AccountBalanceSnapshot, AccountStatementImport, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_statement_routes_require_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/workspaces/1/accounts/1/statements/new", follow_redirects=False
            )
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_accounts_show_import_only_for_supported_types(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                session.add_all(
                    [
                        Account(
                            workspace_id=workspace_id,
                            name="Brokerage",
                            account_type="investment_brokerage",
                            is_liability=False,
                        ),
                        Account(
                            workspace_id=workspace_id,
                            name="Checking",
                            account_type="checking",
                            is_liability=False,
                        ),
                    ]
                )
                session.commit()
                accounts = {
                    account.name: account.id for account in session.scalars(select(Account))
                }
            response = await client.get(f"/workspaces/{workspace_id}/accounts")
            unsupported = await client.get(
                f"/workspaces/{workspace_id}/accounts/{accounts['Checking']}/statements/new"
            )
    finally:
        engine.dispose()
    assert response.status_code == 200
    assert f"/accounts/{accounts['Brokerage']}/statements/new" in response.text
    assert f"/accounts/{accounts['Checking']}/statements/new" not in response.text
    assert unsupported.status_code == 404


@pytest.mark.anyio
async def test_member_uploads_reviews_edits_and_confirms_statement(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                account = Account(
                    workspace_id=workspace_id,
                    name="Brokerage",
                    account_type="investment_brokerage",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            token = client.cookies["wimm_csrf"]
            csv_bytes = (
                b"account_name,institution,account_last_four,total_balance,as_of_date\n"
                b"Northstar Brokerage,Fictional Brokerage,4821,125430.18,2026-07-31\n"
            )
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={
                    "csrf_token": token,
                    "statement_category": "brokerage",
                    "retention_choice": "retain",
                },
                files={"statement_file": ("statement.csv", csv_bytes, "text/csv")},
                follow_redirects=False,
            )
            assert uploaded.status_code == 303
            with factory() as session:
                pending = session.scalar(select(AccountStatementImport))
                assert pending is not None
                pending_id = pending.id
                assert session.scalar(select(AccountBalanceSnapshot.id)) is None
            review = await client.get(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/review"
            )
            assert "125430.18" in review.text
            confirmed = await client.post(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/confirm",
                data={
                    "csrf_token": token,
                    "account_id": str(account_id),
                    "account_name": "Reviewed Brokerage",
                    "institution": "Reviewed Institution",
                    "account_last_four": "4821",
                    "total_balance": "125000.01",
                    "as_of_date": "2026-07-31",
                },
                follow_redirects=False,
            )
            assert confirmed.status_code == 303
            assert confirmed.headers["location"] == f"/workspaces/{workspace_id}/dashboard"
            with factory() as session:
                snapshot = session.scalar(select(AccountBalanceSnapshot))
                assert snapshot is not None
                assert snapshot.balance_cents == 12_500_001
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_statement_mutations_require_csrf(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={"statement_category": "brokerage", "retention_choice": "retain"},
                files={"statement_file": ("statement.csv", b"data", "text/csv")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 403
