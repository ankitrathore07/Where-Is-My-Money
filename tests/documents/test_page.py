from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import User, Workspace, WorkspaceMembership
from tests.route_helpers import build_route_test_app, complete_sign_in, verified_claims


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_unified_document_page_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/documents/new", follow_redirects=False)
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_unified_document_page_hides_a_nonmember_workspace(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as owner_client,
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as nonmember_client,
        ):
            await complete_sign_in(owner_client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            application.state.google_oauth.google.claims = verified_claims(
                sub="document-page-nonmember",
                email="document-page-nonmember@example.test",
                name="Document Page Nonmember",
            )
            await complete_sign_in(nonmember_client)
            with factory() as session:
                nonmember = session.scalar(
                    select(User).where(User.google_sub == "document-page-nonmember")
                )
                assert nonmember is not None
                membership = session.scalar(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id == nonmember.id,
                    )
                )
            response = await nonmember_client.get(f"/workspaces/{workspace_id}/documents/new")
    finally:
        engine.dispose()

    assert membership is None
    assert response.status_code == 404


@pytest.mark.anyio
async def test_unified_document_page_exposes_manual_queue_contract(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.get(f"/workspaces/{workspace_id}/documents/new")
            workspace_page = await client.get(f"/workspaces/{workspace_id}")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert 'id="document-files"' in response.text
    assert "multiple" in response.text
    assert 'id="document-queue-body"' in response.text
    assert 'id="document-category-config"' in response.text
    assert '"retirement_401k_statement"' in response.text
    assert 'value="delete_after_import" checked' in response.text
    assert f"/workspaces/{workspace_id}/imports/new" in response.text
    assert f"/workspaces/{workspace_id}/payslips/new" in response.text
    assert f"/workspaces/{workspace_id}/documents/new" in workspace_page.text
    assert "Upload documents" in workspace_page.text
