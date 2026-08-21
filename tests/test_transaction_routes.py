from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Category, MerchantRule, Transaction, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_transaction_pages_explain_linked_and_deleted_workspace_rules(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                category = session.scalar(select(Category))
                assert workspace is not None and category is not None
                rule = MerchantRule(
                    workspace_id=workspace.id,
                    name="Coffee truth",
                    enabled=True,
                    priority=0,
                    condition_version=1,
                    condition_json={
                        "version": 1,
                        "type": "predicate",
                        "field": "description",
                        "operator": "contains",
                        "value": "COFFEE",
                    },
                    lock_version=1,
                    category_id=category.id,
                )
                session.add(rule)
                session.flush()
                foreign_owner = User(
                    google_sub="foreign-attribution-owner",
                    email="foreign-attribution@example.com",
                )
                session.add(foreign_owner)
                session.flush()
                foreign_workspace = Workspace(
                    name="Foreign attribution",
                    is_personal=True,
                    owner_id=foreign_owner.id,
                )
                session.add(foreign_workspace)
                session.flush()
                foreign_category = Category(
                    workspace_id=foreign_workspace.id,
                    name="Foreign category",
                    name_key="foreign category",
                    kind="expense",
                )
                session.add(foreign_category)
                session.flush()
                foreign_rule = MerchantRule(
                    workspace_id=foreign_workspace.id,
                    name="SECRET FOREIGN ATTRIBUTION",
                    enabled=True,
                    priority=0,
                    condition_version=1,
                    condition_json={
                        "version": 1,
                        "type": "predicate",
                        "field": "description",
                        "operator": "contains",
                        "value": "SECRET",
                    },
                    lock_version=1,
                    category_id=foreign_category.id,
                )
                session.add(foreign_rule)
                session.flush()
                linked = Transaction(
                    workspace_id=workspace.id,
                    date=datetime(2026, 8, 1, tzinfo=UTC),
                    description="Coffee linked",
                    amount_cents=-500,
                    category_id=category.id,
                    categorization_source="workspace_rule",
                    merchant_rule_id=rule.id,
                )
                deleted = Transaction(
                    workspace_id=workspace.id,
                    date=datetime(2026, 8, 2, tzinfo=UTC),
                    description="Coffee preserved",
                    amount_cents=-600,
                    category_id=category.id,
                    categorization_source="workspace_rule",
                    merchant_rule_id=None,
                )
                corrupt_foreign_link = Transaction(
                    workspace_id=workspace.id,
                    date=datetime(2026, 8, 3, tzinfo=UTC),
                    description="Foreign link must stay private",
                    amount_cents=-700,
                    category_id=category.id,
                    categorization_source="workspace_rule",
                    merchant_rule_id=foreign_rule.id,
                )
                session.add_all([linked, deleted, corrupt_foreign_link])
                session.commit()
                workspace_id = workspace.id
                linked_id = linked.id
                rule_id = rule.id
            listing = await client.get(f"/workspaces/{workspace_id}/transactions")
            edit = await client.get(
                f"/workspaces/{workspace_id}/transactions/{linked_id}/categorization"
            )
    finally:
        engine.dispose()

    assert listing.status_code == 200
    assert "Workspace rule" in listing.text
    assert "Deleted workspace rule" in listing.text
    assert "Coffee truth" in listing.text
    assert "SECRET FOREIGN ATTRIBUTION" not in listing.text
    assert f"/workspaces/{workspace_id}/rules/{rule_id}/edit" in listing.text
    assert edit.status_code == 200
    assert "Categorized by" in edit.text
    assert "description contains “COFFEE”" in edit.text


@pytest.mark.anyio
async def test_transaction_list_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/transactions", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_list_shows_only_authorized_workspace_and_hides_nonmember_page(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                owner_workspace = session.scalar(select(Workspace))
                category = session.scalar(select(Category))
                assert owner_workspace is not None and category is not None
                stranger = User(
                    google_sub="transaction-stranger",
                    email="transaction-stranger@example.com",
                    display_name="Stranger",
                )
                session.add(stranger)
                session.flush()
                other_workspace = Workspace(name="Other", is_personal=True, owner_id=stranger.id)
                session.add(other_workspace)
                session.flush()
                session.add_all(
                    [
                        Transaction(
                            workspace_id=owner_workspace.id,
                            date=datetime(2026, 8, 1, tzinfo=UTC),
                            description="Visible market",
                            amount_cents=-1234,
                            category_id=category.id,
                        ),
                        Transaction(
                            workspace_id=other_workspace.id,
                            date=datetime(2026, 8, 1, tzinfo=UTC),
                            description="SECRET OTHER TRANSACTION",
                            amount_cents=-9999,
                            category_id=category.id,
                        ),
                    ]
                )
                session.commit()
                owner_workspace_id = owner_workspace.id
                other_workspace_id = other_workspace.id
            response = await client.get(f"/workspaces/{owner_workspace_id}/transactions")
            forbidden = await client.get(f"/workspaces/{other_workspace_id}/transactions")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "Visible market" in response.text
    assert "SECRET OTHER TRANSACTION" not in response.text
    assert forbidden.status_code == 404
    assert "SECRET OTHER TRANSACTION" not in forbidden.text


@pytest.mark.anyio
async def test_filters_amount_direction_and_pagination_are_rendered(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                category = session.scalar(select(Category))
                assert workspace is not None and category is not None
                for index in range(51):
                    session.add(
                        Transaction(
                            workspace_id=workspace.id,
                            date=datetime(2026, 8, (index % 28) + 1, tzinfo=UTC),
                            description=f"Market item {index}",
                            normalized_merchant="MARKET",
                            amount_cents=-1234,
                            category_id=category.id,
                        )
                    )
                session.commit()
                workspace_id = workspace.id
                category_id = category.id
            response = await client.get(
                f"/workspaces/{workspace_id}/transactions",
                params={
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-31",
                    "category_id": str(category_id),
                    "direction": "expense",
                    "q": "market",
                    "page": "1",
                },
            )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert 'value="2026-08-01"' in response.text
    assert 'value="2026-08-31"' in response.text
    assert f'value="{category_id}" selected' in response.text
    assert 'value="expense" selected' in response.text
    assert 'value="market"' in response.text
    assert "$12.34" in response.text
    assert "Money out" in response.text
    assert "page=2" in response.text
    assert "category_id=" in response.text
    assert "q=market" in response.text


@pytest.mark.anyio
async def test_invalid_filter_renders_specific_422_without_private_rows(tmp_path: Path) -> None:
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
            response = await client.get(
                f"/workspaces/{workspace_id}/transactions", params={"q": "S" * 101}
            )
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert "Search must be 100 characters or fewer" in response.text
    assert "SECRET OTHER TRANSACTION" not in response.text


@pytest.mark.anyio
async def test_subscription_filter_is_rendered_and_preserved(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                category = session.scalar(select(Category))
                assert workspace is not None and category is not None
                for index in range(51):
                    session.add(
                        Transaction(
                            workspace_id=workspace.id,
                            date=datetime(2026, 8, 1, tzinfo=UTC),
                            description=f"Subscription {index}",
                            amount_cents=-999,
                            category_id=category.id,
                            is_subscription=True,
                        )
                    )
                session.add(
                    Transaction(
                        workspace_id=workspace.id,
                        date=datetime(2026, 8, 2, tzinfo=UTC),
                        description="Not recurring",
                        amount_cents=-500,
                        category_id=category.id,
                        is_subscription=False,
                    )
                )
                session.commit()
                workspace_id = workspace.id
            response = await client.get(
                f"/workspaces/{workspace_id}/transactions",
                params={"subscription": "yes"},
            )
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert 'value="yes" selected' in response.text
    assert "Subscription 50" in response.text
    assert "Not recurring" not in response.text
    assert "subscription=yes" in response.text
