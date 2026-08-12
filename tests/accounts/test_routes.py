from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.accounts import routes as account_routes
from app.db.models import Account, AccountBalanceSnapshot, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_accounts_require_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/accounts", follow_redirects=False)
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_member_can_create_edit_and_add_manual_balance(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                workspace_id = workspace.id
            token = client.cookies["wimm_csrf"]
            created = await client.post(
                f"/workspaces/{workspace_id}/accounts",
                data={
                    "csrf_token": token,
                    "name": "Everyday Checking",
                    "account_type": "checking",
                    "institution": "Example CU",
                    "classification": "asset",
                },
                follow_redirects=False,
            )
            assert created.status_code == 303
            with factory() as session:
                account = session.scalar(select(Account))
                assert account is not None
                account_id = account.id
            edited = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}",
                data={
                    "csrf_token": token,
                    "name": "Everyday Checking Updated",
                    "account_type": "checking",
                    "institution": "Example CU",
                    "classification": "asset",
                },
                follow_redirects=False,
            )
            assert edited.status_code == 303
            with factory() as session:
                updated = session.get(Account, account_id)
                assert updated is not None
                assert updated.name == "Everyday Checking Updated"
            balance = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                data={
                    "csrf_token": token,
                    "amount": "8420.50",
                    "as_of_date": "2026-08-10",
                },
                follow_redirects=False,
            )
            assert balance.status_code == 303
            with factory() as session:
                saved = session.scalar(select(AccountBalanceSnapshot))
                assert saved is not None
                assert saved.balance_cents == 842_050
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_manual_balance_uses_utc_calendar_date_for_form_max_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LocalDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 8, 10)

    class UtcDatetime(datetime):
        @classmethod
        def now(cls, tz: object | None = None) -> datetime:
            assert tz is UTC
            return datetime(2026, 8, 11, 0, 30, tzinfo=UTC)

    monkeypatch.setattr(account_routes, "date", LocalDate)
    monkeypatch.setattr(account_routes, "datetime", UtcDatetime, raising=False)
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = Account(
                    workspace_id=workspace_id,
                    name="Everyday Checking",
                    account_type="checking",
                    institution="Example CU",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            form = await client.get(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances/new"
            )
            accepted = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "amount": "100.00",
                    "as_of_date": "2026-08-11",
                },
                follow_redirects=False,
            )
            rejected = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "amount": "101.00",
                    "as_of_date": "2026-08-12",
                },
            )
            with factory() as session:
                snapshots = tuple(
                    session.scalars(
                        select(AccountBalanceSnapshot).where(
                            AccountBalanceSnapshot.account_id == account_id
                        )
                    )
                )
    finally:
        engine.dispose()

    assert 'max="2026-08-11"' in form.text
    assert accepted.status_code == 303
    assert rejected.status_code == 422
    assert "Balance dates cannot be in the future." in rejected.text
    assert [(snapshot.balance_cents, snapshot.as_of_date) for snapshot in snapshots] == [
        (10_000, date(2026, 8, 11))
    ]


@pytest.mark.anyio
async def test_account_create_requires_csrf_and_redisplays_name_validation(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            missing_csrf = await client.post(f"/workspaces/{workspace_id}/accounts", data={})
            invalid = await client.post(
                f"/workspaces/{workspace_id}/accounts",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "   ",
                    "account_type": "checking",
                    "institution": "Example CU",
                    "classification": "asset",
                },
            )
            with factory() as session:
                account_count = session.scalar(select(func.count()).select_from(Account))
    finally:
        engine.dispose()

    assert missing_csrf.status_code == 403
    assert invalid.status_code == 422
    assert "Account name is required." in invalid.text
    assert account_count == 0


@pytest.mark.anyio
async def test_account_update_rejects_missing_csrf_without_mutating_account(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = Account(
                    workspace_id=workspace_id,
                    name="Original Name",
                    account_type="checking",
                    institution="Example CU",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            response = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}",
                data={
                    "name": "Changed Name",
                    "account_type": "savings",
                    "institution": "Changed Bank",
                    "classification": "liability",
                },
            )
            with factory() as session:
                unchanged = session.get(Account, account_id)
    finally:
        engine.dispose()

    assert response.status_code == 403
    assert unchanged is not None
    assert (
        unchanged.name,
        unchanged.account_type,
        unchanged.institution,
        unchanged.is_liability,
    ) == ("Original Name", "checking", "Example CU", False)


@pytest.mark.anyio
async def test_manual_balance_rejects_missing_csrf_without_creating_snapshot(
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
                account = Account(
                    workspace_id=workspace_id,
                    name="Everyday Checking",
                    account_type="checking",
                    institution="Example CU",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            response = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                data={"amount": "100.00", "as_of_date": "2026-08-10"},
            )
            with factory() as session:
                snapshot_count = session.scalar(
                    select(func.count())
                    .select_from(AccountBalanceSnapshot)
                    .where(AccountBalanceSnapshot.account_id == account_id)
                )
    finally:
        engine.dispose()

    assert response.status_code == 403
    assert snapshot_count == 0


@pytest.mark.anyio
async def test_invalid_account_classification_is_rejected(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/accounts",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Checking",
                    "account_type": "checking",
                    "institution": "Example CU",
                    "classification": "maybe",
                },
            )
            with factory() as session:
                account_count = session.scalar(select(func.count()).select_from(Account))
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert "Choose asset or liability." in response.text
    assert account_count == 0


@pytest.mark.anyio
async def test_foreign_account_routes_return_generic_not_found(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                other_user = User(
                    google_sub="foreign-account-owner", email="foreign-owner@example.com"
                )
                other_workspace = Workspace(
                    name="Foreign workspace", is_personal=True, owner=other_user
                )
                session.add(other_workspace)
                session.flush()
                foreign_account = Account(
                    workspace_id=other_workspace.id,
                    name="SECRET OTHER ACCOUNT",
                    account_type="savings",
                    institution="Secret Bank",
                    is_liability=False,
                )
                session.add(foreign_account)
                session.flush()
                session.add(
                    AccountBalanceSnapshot(
                        workspace_id=other_workspace.id,
                        account_id=foreign_account.id,
                        balance_cents=1_234_500,
                        as_of_date=date(2026, 8, 10),
                        source="manual",
                    )
                )
                session.commit()
                account_id = foreign_account.id
            token = client.cookies["wimm_csrf"]
            responses = (
                await client.get(f"/workspaces/{workspace_id}/accounts/{account_id}/edit"),
                await client.get(f"/workspaces/{workspace_id}/accounts/{account_id}/balances/new"),
                await client.post(
                    f"/workspaces/{workspace_id}/accounts/{account_id}",
                    data={
                        "csrf_token": token,
                        "name": "Changed",
                        "account_type": "savings",
                        "institution": "Bank",
                        "classification": "asset",
                    },
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                    data={"csrf_token": token, "amount": "999.00", "as_of_date": "2026-08-10"},
                ),
            )
            with factory() as session:
                foreign_account = session.get(Account, account_id)
                snapshot_count = session.scalar(
                    select(func.count())
                    .select_from(AccountBalanceSnapshot)
                    .where(AccountBalanceSnapshot.account_id == account_id)
                )
    finally:
        engine.dispose()

    assert all(response.status_code == 404 for response in responses)
    assert all("SECRET OTHER ACCOUNT" not in response.text for response in responses)
    assert all("12345.00" not in response.text for response in responses)
    assert foreign_account is not None
    assert foreign_account.name == "SECRET OTHER ACCOUNT"
    assert snapshot_count == 1
