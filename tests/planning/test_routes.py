from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import Budget, Category, Transaction, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_planning_requires_authentication_and_workspace_membership(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is None
            unauthenticated = await client.get("/workspaces/1/planning", follow_redirects=False)
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                other_user = User(
                    google_sub="planning-foreign", email="planning-foreign@example.com"
                )
                foreign_workspace = Workspace(
                    name="SECRET PLANNING", is_personal=True, owner=other_user
                )
                session.add(foreign_workspace)
                session.commit()
                foreign_id = foreign_workspace.id
            forbidden = await client.get(
                f"/workspaces/{foreign_id}/planning", follow_redirects=False
            )
    finally:
        engine.dispose()

    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["location"] == "/"
    assert forbidden.status_code == 404
    assert "SECRET PLANNING" not in forbidden.text


@pytest.mark.anyio
async def test_planning_month_shows_explainable_suggestion_without_writing(
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
                category = Category(workspace_id=workspace_id, name="Travel", kind="expense")
                session.add(category)
                session.flush()
                for month, cents in ((5, 10_000), (6, 30_000), (7, 20_001)):
                    session.add(
                        Transaction(
                            workspace_id=workspace_id,
                            category_id=category.id,
                            date=datetime(2026, month, 5, tzinfo=UTC),
                            description=f"Travel {month}",
                            amount_cents=-cents,
                            categorization_source="manual",
                        )
                    )
                session.commit()
            first = await client.get(f"/workspaces/{workspace_id}/planning?month=2026-08")
            second = await client.get(f"/workspaces/{workspace_id}/planning?month=2026-08")
            invalid = await client.get(f"/workspaces/{workspace_id}/planning?month=2026-8")
            with factory() as session:
                budget_count = session.scalar(select(func.count()).select_from(Budget))
    finally:
        engine.dispose()

    assert first.status_code == second.status_code == 200
    assert "August 2026" in first.text
    assert "May 1–July 31, 2026" in first.text
    assert "Travel" in first.text
    assert "$220.01" in first.text
    assert "Accept suggestion" in first.text
    assert invalid.status_code == 422
    assert "Use a valid month in YYYY-MM format." in invalid.text
    assert budget_count == 0


@pytest.mark.anyio
async def test_budget_post_requires_csrf_then_creates_and_edits_one_row(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                category = Category(workspace_id=workspace_id, name="Travel", kind="expense")
                session.add(category)
                session.commit()
                category_id = category.id
            missing_csrf = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "category_id": str(category_id),
                    "period_month": "2026-08",
                    "amount": "220.01",
                },
            )
            created = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "category_id": str(category_id),
                    "period_month": "2026-08",
                    "amount": "220.01",
                },
                follow_redirects=False,
            )
            with factory() as session:
                first = session.scalar(select(Budget))
                assert first is not None
                first_id = first.id
            edited = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "category_id": str(category_id),
                    "period_month": "2026-08",
                    "amount": "200.00",
                },
                follow_redirects=False,
            )
            with factory() as session:
                budgets = tuple(session.scalars(select(Budget)))
    finally:
        engine.dispose()

    assert missing_csrf.status_code == 403
    assert created.status_code == edited.status_code == 303
    assert created.headers["location"].endswith("/planning?month=2026-08")
    assert len(budgets) == 1
    assert budgets[0].id == first_id
    assert budgets[0].amount_cents == 20_000


@pytest.mark.anyio
async def test_budget_post_rejects_invalid_and_foreign_values_without_leak(
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
                foreign_user = User(google_sub="budget-foreign", email="budget-foreign@example.com")
                foreign_workspace = Workspace(name="Foreign", is_personal=True, owner=foreign_user)
                session.add(foreign_workspace)
                session.flush()
                foreign_category = Category(
                    workspace_id=foreign_workspace.id,
                    name="SECRET FOREIGN BUDGET",
                    kind="expense",
                )
                session.add(foreign_category)
                session.commit()
                foreign_category_id = foreign_category.id
            invalid = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "category_id": "not-an-id",
                    "period_month": "2026-8",
                    "amount": "1.001",
                },
            )
            foreign = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "category_id": str(foreign_category_id),
                    "period_month": "2026-08",
                    "amount": "100.00",
                },
            )
            with factory() as session:
                budget_count = session.scalar(select(func.count()).select_from(Budget))
    finally:
        engine.dispose()

    assert invalid.status_code == 422
    assert "Use a valid month in YYYY-MM format." in invalid.text
    assert foreign.status_code == 404
    assert "SECRET FOREIGN BUDGET" not in foreign.text
    assert budget_count == 0


@pytest.mark.anyio
async def test_blank_budget_amount_returns_the_editable_planning_page(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                category = Category(workspace_id=workspace_id, name="Travel", kind="expense")
                session.add(category)
                session.commit()
                category_id = category.id
            response = await client.post(
                f"/workspaces/{workspace_id}/planning/budgets",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "category_id": str(category_id),
                    "period_month": "2026-08",
                    "amount": "",
                },
            )
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Enter a dollar amount with at most two decimals." in response.text
    assert "Monthly budget · August 2026" in response.text
