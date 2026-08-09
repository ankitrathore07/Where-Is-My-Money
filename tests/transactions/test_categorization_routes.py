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


def _seed_transaction(factory) -> tuple[int, int, int]:
    with factory() as session:
        workspace = session.scalar(select(Workspace))
        assert workspace is not None
        category = Category(
            workspace_id=workspace.id,
            name="Dining & Drinks",
            name_key="dining & drinks",
            kind="expense",
        )
        transaction = Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 8, 9, tzinfo=UTC),
            description="LOCAL CAFE 123",
            normalized_merchant="Local Cafe",
            amount_cents=-2450,
            category=category,
            categorization_source="uncategorized",
        )
        session.add(transaction)
        session.commit()
        return workspace.id, category.id, transaction.id


@pytest.mark.anyio
async def test_categorization_page_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/workspaces/1/transactions/1/categorization",
                follow_redirects=False,
            )
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_owner_can_open_and_submit_manual_categorization(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            workspace_id, category_id, transaction_id = _seed_transaction(factory)
            transaction_list = await client.get(f"/workspaces/{workspace_id}/transactions")
            page = await client.get(
                f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization"
            )
            response = await client.post(
                f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "normalized_merchant": "Neighborhood Cafe",
                    "category_id": str(category_id),
                    "is_subscription": "on",
                    "save_for_future": "on",
                },
                follow_redirects=False,
            )
            with factory() as session:
                transaction = session.get(Transaction, transaction_id)
                rule = session.scalar(
                    select(MerchantRule).where(MerchantRule.workspace_id == workspace_id)
                )
    finally:
        engine.dispose()

    assert page.status_code == 200
    assert (
        f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization"
        in transaction_list.text
    )
    assert "LOCAL CAFE 123" in page.text
    assert "$24.50" in page.text
    assert "Neighborhood Cafe" not in page.text
    assert response.status_code == 303
    assert response.headers["location"] == f"/workspaces/{workspace_id}/transactions"
    assert transaction is not None and rule is not None
    assert transaction.description == "LOCAL CAFE 123"
    assert transaction.normalized_merchant == "Neighborhood Cafe"
    assert transaction.category_id == category_id
    assert transaction.is_subscription is True
    assert transaction.categorization_source == "manual"
    assert rule.merchant_pattern == "LOCAL CAFE 123"
    assert rule.is_subscription is True


@pytest.mark.anyio
async def test_categorization_post_requires_csrf_and_redisplays_validation(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            workspace_id, category_id, transaction_id = _seed_transaction(factory)
            missing_csrf = await client.post(
                f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization",
                data={"normalized_merchant": "Changed", "category_id": str(category_id)},
            )
            invalid = await client.post(
                f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "normalized_merchant": "   ",
                    "category_id": str(category_id),
                },
            )
            with factory() as session:
                transaction = session.get(Transaction, transaction_id)
    finally:
        engine.dispose()

    assert missing_csrf.status_code == 403
    assert invalid.status_code == 422
    assert "Merchant name is required" in invalid.text
    assert transaction is not None
    assert transaction.normalized_merchant == "Local Cafe"
    assert transaction.categorization_source == "uncategorized"


@pytest.mark.anyio
async def test_cross_workspace_transaction_and_category_return_404(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            workspace_id, _, transaction_id = _seed_transaction(factory)
            with factory() as session:
                stranger = User(google_sub="edit-stranger", email="edit-stranger@example.com")
                private = Workspace(name="Private", is_personal=True, owner=stranger)
                foreign_category = Category(
                    workspace=private, name="Private", name_key="private", kind="expense"
                )
                session.add(foreign_category)
                session.commit()
                private_id = private.id
                foreign_category_id = foreign_category.id
            foreign_page = await client.get(
                f"/workspaces/{private_id}/transactions/{transaction_id}/categorization"
            )
            foreign_category_response = await client.post(
                f"/workspaces/{workspace_id}/transactions/{transaction_id}/categorization",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "normalized_merchant": "Local Cafe",
                    "category_id": str(foreign_category_id),
                },
            )
    finally:
        engine.dispose()

    assert foreign_page.status_code == 404
    assert foreign_category_response.status_code == 404
