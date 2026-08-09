from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import Category, User, Workspace, WorkspaceMembership
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_category_page_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/categories", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_owner_can_list_and_create_custom_category(tmp_path: Path) -> None:
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
            page = await client.get(f"/workspaces/{workspace_id}/categories")
            response = await client.post(
                f"/workspaces/{workspace_id}/categories",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "  Weekend   Trips  ",
                    "kind": "expense",
                },
                follow_redirects=False,
            )
            with factory() as session:
                created = session.scalar(
                    select(Category).where(Category.workspace_id == workspace_id)
                )
    finally:
        engine.dispose()

    assert page.status_code == 200
    assert "Workspace categories" in page.text
    assert "Built-in categories" in page.text
    assert response.status_code == 303
    assert response.headers["location"] == f"/workspaces/{workspace_id}/categories"
    assert created is not None
    assert created.name == "Weekend Trips"


@pytest.mark.anyio
async def test_accepted_member_can_manage_categories_but_nonmember_gets_404(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                current = session.scalar(
                    select(User).where(User.email == "import-route@example.com")
                )
                stranger = User(google_sub="category-stranger", email="stranger@example.com")
                member_workspace = Workspace(name="Shared", is_personal=False, owner=stranger)
                private_workspace = Workspace(name="Private", is_personal=True, owner=stranger)
                session.add_all([member_workspace, private_workspace])
                session.flush()
                assert current is not None
                session.add(
                    WorkspaceMembership(
                        workspace_id=member_workspace.id, user_id=current.id, role="member"
                    )
                )
                session.commit()
                member_id = member_workspace.id
                private_id = private_workspace.id
            member_response = await client.get(f"/workspaces/{member_id}/categories")
            private_response = await client.get(f"/workspaces/{private_id}/categories")
    finally:
        engine.dispose()

    assert member_response.status_code == 200
    assert private_response.status_code == 404


@pytest.mark.anyio
async def test_category_post_requires_csrf_and_redisplays_validation(tmp_path: Path) -> None:
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
            missing_csrf = await client.post(
                f"/workspaces/{workspace_id}/categories",
                data={"name": "Trips", "kind": "expense"},
            )
            invalid = await client.post(
                f"/workspaces/{workspace_id}/categories",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "   ",
                    "kind": "expense",
                },
            )
            with factory() as session:
                custom_count = session.scalar(
                    select(func.count())
                    .select_from(Category)
                    .where(Category.workspace_id.is_not(None))
                )
    finally:
        engine.dispose()

    assert missing_csrf.status_code == 403
    assert invalid.status_code == 422
    assert "Category name is required" in invalid.text
    assert custom_count == 0


@pytest.mark.anyio
async def test_category_uniqueness_race_redisplays_duplicate_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)

    def lose_unique_race(*args, **kwargs):
        raise IntegrityError("insert", {}, RuntimeError("unique constraint"))

    monkeypatch.setattr("app.categories.routes.create_custom_category", lose_unique_race)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/categories",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Trips",
                    "kind": "expense",
                },
            )
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert "already exists in this workspace" in response.text
