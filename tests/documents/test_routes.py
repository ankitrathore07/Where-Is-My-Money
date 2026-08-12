from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import (
    AccountStatementImport,
    ImportJob,
    Payslip,
    UploadedFile,
    User,
    Workspace,
)
from app.payslips.extraction import ExtractedText
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"
STATEMENT_CSV_BYTES = (
    b"account_name,institution,account_last_four,total_balance,as_of_date\n"
    b"Example account,Example institution,1234,12345.67,2026-08-01\n"
)
PDF_BYTES = b"%PDF-synthetic-document-route"
PAY_TEXT = """Employer: Northstar Bicycle Works
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Net Pay: $3,700.00
Taxes: $900.00
Deductions: $400.00
"""


class RouteFakeExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(PAY_TEXT, "embedded_text")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_document_upload_requires_csrf_before_storage(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={"category_key": "transaction_statement", "retention_choice": "retain"},
                files={"document": ("checking.csv", CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()
    assert response.status_code == 403
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
@pytest.mark.parametrize("category_key", ["unlisted", "missing"])
async def test_unprocessable_category_never_reaches_private_storage(
    tmp_path: Path, category_key: str
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": category_key,
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("account.pdf", b"private", "application/pdf")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
async def test_document_upload_requires_exactly_one_file(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
            )
    finally:
        engine.dispose()
    assert response.status_code == 422


@pytest.mark.anyio
async def test_document_upload_rejects_multiple_files_before_storage(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files=[
                    ("document", ("checking.csv", CSV_BYTES, "text/csv")),
                    ("document", ("savings.csv", CSV_BYTES, "text/csv")),
                ],
            )
        with factory() as session:
            assert session.scalar(select(func.count(ImportJob.id))) == 0
            assert session.scalar(select(func.count(Payslip.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "code": "invalid_file_count",
        "message": "Upload exactly one document.",
    }
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
async def test_document_upload_rejects_file_under_unexpected_field_before_storage(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files=[
                    ("document", ("checking.csv", CSV_BYTES, "text/csv")),
                    ("unexpected", ("private.pdf", b"%PDF-private", "application/pdf")),
                ],
            )
        with factory() as session:
            assert session.scalar(select(func.count(ImportJob.id))) == 0
            assert session.scalar(select(func.count(Payslip.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert response.json() == {
        "ok": False,
        "code": "invalid_file_count",
        "message": "Upload exactly one document.",
    }
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
async def test_document_upload_hides_a_foreign_workspace(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                foreign_owner = User(
                    google_sub="foreign-document-owner",
                    email="foreign-document-owner@example.test",
                )
                foreign = Workspace(name="Foreign", is_personal=True, owner=foreign_owner)
                session.add_all([foreign_owner, foreign])
                session.commit()
                foreign_id = foreign.id
            response = await client.post(
                f"/workspaces/{foreign_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("checking.csv", CSV_BYTES, "text/csv")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 404
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_category_format_mismatch_does_not_store_the_file(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("checking.pdf", b"%PDF-private", "application/pdf")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 400
    assert response.json()["code"] == "category_format_mismatch"
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category_key,filename,content_type,body,next_label,path_fragment,model",
    [
        (
            "transaction_statement",
            "checking.csv",
            "text/csv",
            CSV_BYTES,
            "Map columns",
            "/imports/",
            ImportJob,
        ),
        (
            "payslip",
            "pay.pdf",
            "application/pdf",
            PDF_BYTES,
            "Review payslip",
            "/payslips/",
            Payslip,
        ),
    ],
)
async def test_supported_document_returns_its_existing_review_destination(
    tmp_path: Path,
    category_key: str,
    filename: str,
    content_type: str,
    body: bytes,
    next_label: str,
    path_fragment: str,
    model: type,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.payslip_extractor = RouteFakeExtractor()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": category_key,
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": (filename, body, content_type)},
            )
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(model)) == 1
            uploaded = session.scalar(select(UploadedFile))
            assert uploaded is not None
            assert uploaded.retention_choice == "retain"
    finally:
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"] == "Ready for review."
    assert payload["next_label"] == next_label
    assert path_fragment in payload["next_url"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category_key,statement_category",
    [
        ("retirement_401k_statement", "investment_401k"),
        ("brokerage_statement", "brokerage"),
        ("mortgage_statement", "mortgage"),
        ("loan_statement", "loan"),
        ("other_account_statement", "other"),
    ],
)
async def test_supported_account_statement_returns_balance_review_destination(
    tmp_path: Path, category_key: str, statement_category: str
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": category_key,
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("balance.csv", STATEMENT_CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            pending = session.scalar(select(AccountStatementImport))
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert pending is not None
    assert pending.statement_category == statement_category
    assert response.json() == {
        "ok": True,
        "message": "Ready for review.",
        "next_url": f"/workspaces/{workspace_id}/statement-imports/{pending.id}/review",
        "next_label": "Review balance",
    }


@pytest.mark.anyio
async def test_retried_payslip_upload_returns_the_existing_review_destination(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.payslip_extractor = RouteFakeExtractor()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            request = {
                "category_key": "payslip",
                "retention_choice": "retain",
                "csrf_token": token,
            }
            first = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data=request,
                files={"document": ("pay.pdf", PDF_BYTES, "application/pdf")},
            )
            retried = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={**request, "retention_choice": "delete_after_import"},
                files={"document": ("pay.pdf", PDF_BYTES, "application/pdf")},
            )
        with factory() as session:
            payslip_count = session.scalar(select(func.count(Payslip.id)))
            upload_count = session.scalar(select(func.count(UploadedFile.id)))
            uploaded_file = session.scalar(select(UploadedFile))
    finally:
        engine.dispose()

    assert first.status_code == 200
    assert retried.status_code == 200
    assert retried.json()["next_url"] == first.json()["next_url"]
    assert (payslip_count, upload_count) == (1, 1)
    assert uploaded_file is not None
    assert uploaded_file.retention_choice == "retain"
    assert len(list(tmp_path.rglob("*.pdf"))) == 1


@pytest.mark.anyio
async def test_malformed_csv_returns_safe_error_without_storage_or_record(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("empty.csv", b"", "text/csv")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(ImportJob.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "code": "empty_file",
        "message": "Choose a CSV file that contains headers.",
    }
    assert list(tmp_path.rglob("*.csv")) == []
