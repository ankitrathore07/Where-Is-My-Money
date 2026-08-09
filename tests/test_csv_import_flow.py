from pathlib import Path
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import ImportJob, Transaction, UploadedFile, Workspace
from tests.route_helpers import (
    build_route_test_app,
    complete_sign_in,
    csrf_token,
    verified_claims,
)

STATEMENT = Path(__file__).parent / "fixtures" / "statements" / "synthetic_checking.csv"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_reviewed_csv_import_to_filtered_list_and_safe_reupload(tmp_path: Path) -> None:
    source = STATEMENT.read_bytes()
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
            await complete_sign_in(owner_client)
            token = await csrf_token(owner_client)
            with factory() as session:
                owner_workspace = session.scalar(select(Workspace))
                assert owner_workspace is not None
                workspace_id = owner_workspace.id

            upload_form = await owner_client.get(f"/workspaces/{workspace_id}/imports/new")
            uploaded = await owner_client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic_checking.csv", source, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            mapping = await owner_client.get(uploaded.headers["location"])
            with factory() as session:
                assert session.scalar(select(func.count()).select_from(ImportJob)) == 1
                assert session.scalar(select(func.count()).select_from(Transaction)) == 0
                stored = session.scalar(select(UploadedFile))
                assert stored is not None and stored.deleted is False
            mapped = await owner_client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Posted",
                    "description_column": "Details",
                    "amount_mode": "split",
                    "amount_column": "",
                    "debit_column": "Debit",
                    "credit_column": "Credit",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
                follow_redirects=False,
            )
            review = await owner_client.get(mapped.headers["location"])
            commit_body = urlencode(
                [
                    ("csrf_token", token),
                    ("row_numbers", "2"),
                    ("include_2", "on"),
                    ("date_2", "2026-08-01"),
                    ("description_2", "Example Grocery"),
                    ("amount_2", "-12.34"),
                    ("row_numbers", "3"),
                    ("include_3", "on"),
                    ("date_3", "2026-08-02"),
                    ("description_3", "EXAMPLE PAYROLL"),
                    ("amount_3", "2500.00"),
                    ("row_numbers", "4"),
                    ("date_4", "2026-08-03"),
                    ("description_4", "EXAMPLE COFFEE"),
                    ("amount_4", "-4.50"),
                ]
            )
            committed = await owner_client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/commit",
                content=commit_body,
                headers={"content-type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            with factory() as session:
                job = session.get(ImportJob, import_id)
                stored = session.scalar(select(UploadedFile))
                assert session.scalar(select(func.count()).select_from(Transaction)) == 2
                assert job is not None and job.status == "committed"
                assert stored is not None and stored.deleted is True

            listed = await owner_client.get(f"/workspaces/{workspace_id}/transactions")
            filtered = await owner_client.get(
                f"/workspaces/{workspace_id}/transactions",
                params={"direction": "expense", "q": "grocery"},
            )
            reuploaded = await owner_client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic_checking.csv", source, "text/csv")},
                follow_redirects=False,
            )
            with factory() as session:
                assert session.scalar(select(func.count()).select_from(ImportJob)) == 1
                assert session.scalar(select(func.count()).select_from(Transaction)) == 2
                assert session.scalar(select(func.count()).select_from(UploadedFile)) == 1

            application.state.google_oauth.google.claims = verified_claims(
                sub="acceptance-other", email="acceptance-other@example.com", name="Other"
            )
            await complete_sign_in(other_client)
            hidden_import = await other_client.get(
                f"/workspaces/{workspace_id}/imports/{import_id}"
            )
            hidden_transactions = await other_client.get(f"/workspaces/{workspace_id}/transactions")
    finally:
        engine.dispose()

    assert upload_form.status_code == 200
    assert f'value="{token}"' in upload_form.text
    assert uploaded.status_code == 303
    assert mapping.status_code == 200
    assert mapped.status_code == 303
    assert review.status_code == 200
    assert all(value in review.text for value in ("-12.34", "2500.00", "-4.50"))
    assert committed.status_code == 303
    assert list(tmp_path.rglob("*.csv")) == []
    assert "Example Grocery" in listed.text
    assert "EXAMPLE PAYROLL" in listed.text
    assert "EXAMPLE COFFEE" not in listed.text
    assert "Example Grocery" in filtered.text
    assert "EXAMPLE PAYROLL" not in filtered.text
    assert reuploaded.status_code == 303
    assert reuploaded.headers["location"].endswith("/transactions?already_imported=1")
    assert hidden_import.status_code == 404
    assert hidden_transactions.status_code == 404
