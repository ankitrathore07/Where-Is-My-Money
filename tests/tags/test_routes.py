from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Tag, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_user_can_create_rename_and_delete_workspace_tag(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                session.add(Tag(workspace_id=None, name="Essential"))
                session.commit()
            created = await client.post(
                f"/workspaces/{workspace_id}/tags",
                data={"csrf_token": client.cookies["wimm_csrf"], "name": "Family Priority"},
                follow_redirects=False,
            )
            with factory() as session:
                custom = session.scalar(select(Tag).where(Tag.workspace_id == workspace_id))
                assert custom is not None
                custom_id = custom.id
            renamed = await client.post(
                f"/workspaces/{workspace_id}/tags/{custom_id}",
                data={"csrf_token": client.cookies["wimm_csrf"], "name": "Family Support"},
                follow_redirects=False,
            )
            page = await client.get(f"/workspaces/{workspace_id}/tags")
            deleted = await client.post(
                f"/workspaces/{workspace_id}/tags/{custom_id}/delete",
                data={"csrf_token": client.cookies["wimm_csrf"]},
                follow_redirects=False,
            )
            with factory() as session:
                assert session.get(Tag, custom_id) is None
    finally:
        engine.dispose()

    assert created.status_code == 303
    assert renamed.status_code == 303
    assert deleted.status_code == 303
    assert "Family Support" in page.text
    assert "Essential" in page.text


@pytest.mark.anyio
async def test_builtin_and_foreign_tags_cannot_be_changed(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                builtin = Tag(workspace_id=None, name="Vehicle")
                session.add(builtin)
                session.commit()
                workspace_id = workspace.id
                builtin_id = builtin.id
            response = await client.post(
                f"/workspaces/{workspace_id}/tags/{builtin_id}",
                data={"csrf_token": client.cookies["wimm_csrf"], "name": "Changed"},
            )
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert "Built-in tags cannot be changed" in response.text
