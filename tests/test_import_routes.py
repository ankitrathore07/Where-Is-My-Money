from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import ImportJob, Transaction, UploadedFile, User, Workspace
from tests.route_helpers import (
    build_route_test_app,
    complete_sign_in,
    csrf_token,
    verified_claims,
)

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_new_import_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/imports/new", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_upload_requires_csrf_before_file_or_database_mutation(tmp_path: Path) -> None:
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
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import"},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ImportJob)) == 0
            assert session.scalar(select(func.count()).select_from(UploadedFile)) == 0
    finally:
        engine.dispose()

    assert response.status_code == 403
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_valid_upload_maps_and_previews_before_commit(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            mapping_page = await client.get(uploaded.headers["location"])
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
                follow_redirects=False,
            )
            review = await client.get(mapped.headers["location"])
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert mapping_page.status_code == 200
    assert "Date" in mapping_page.text
    assert "Example Market" in mapping_page.text
    assert mapped.status_code == 303
    assert review.status_code == 200
    assert "-12.34" in review.text


@pytest.mark.anyio
async def test_invalid_extension_creates_no_private_record(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.txt", CSV_BYTES, "text/plain")},
            )
        with factory() as session:
            count = session.scalar(select(func.count()).select_from(ImportJob))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert count == 0
    assert "Choose a CSV file" in response.text
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_import_id_is_hidden_from_another_workspace(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as owner_client,
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as other_client,
        ):
            application.state.google_oauth.google.claims = verified_claims(
                sub="owner-import-sub", email="owner-import@example.com", name="Owner"
            )
            await complete_sign_in(owner_client)
            owner_token = await csrf_token(owner_client)
            with factory() as session:
                owner = session.scalar(select(User).where(User.google_sub == "owner-import-sub"))
                assert owner is not None
                owner_workspace_id = owner.owned_workspaces[0].id
            uploaded = await owner_client.post(
                f"/workspaces/{owner_workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": owner_token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])

            application.state.google_oauth.google.claims = verified_claims(
                sub="other-import-sub", email="other-import@example.com", name="Other"
            )
            await complete_sign_in(other_client)
            with factory() as session:
                other = session.scalar(select(User).where(User.google_sub == "other-import-sub"))
                assert other is not None
                other_workspace_id = other.owned_workspaces[0].id
            response = await other_client.get(
                f"/workspaces/{other_workspace_id}/imports/{import_id}/mapping"
            )
    finally:
        engine.dispose()

    assert response.status_code == 404


@pytest.mark.anyio
async def test_review_commit_writes_transactions_then_deletes_source(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
            )
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/commit",
                data={
                    "csrf_token": token,
                    "row_numbers": "2",
                    "include_2": "on",
                    "date_2": "2026-08-01",
                    "description_2": "Example Market",
                    "amount_2": "-12.34",
                },
                follow_redirects=False,
            )
        with factory() as session:
            transaction = session.scalar(select(Transaction))
            job = session.get(ImportJob, import_id)
            uploaded_file = session.scalar(select(UploadedFile))
            assert transaction is not None
            assert transaction.amount_cents == -1234
            assert job is not None and job.status == "committed"
            assert uploaded_file is not None and uploaded_file.deleted is True
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == (f"/workspaces/{workspace_id}/imports/{import_id}")
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_cancel_deletes_source_and_records_canceled_state(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/cancel",
                data={"csrf_token": token},
                follow_redirects=False,
            )
        with factory() as session:
            job = session.get(ImportJob, import_id)
            uploaded_file = session.scalar(select(UploadedFile))
            assert job is not None and job.status == "canceled"
            assert uploaded_file is not None and uploaded_file.deleted is True
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == f"/workspaces/{workspace_id}"
    assert list(tmp_path.rglob("*.csv")) == []
