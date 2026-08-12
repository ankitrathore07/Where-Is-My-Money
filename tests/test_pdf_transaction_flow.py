from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import Category, ImportJob, Transaction, UploadedFile, Workspace
from app.payslips.extraction import DocumentExtractor
from app.statement_imports.extraction import StatementDocumentExtractor
from tests.payslips.pdf_helpers import make_text_pdf
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token


class UnexpectedOcr:
    def extract_png(self, image_bytes: bytes) -> str:
        raise AssertionError("A text PDF must not invoke OCR")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_text_pdf_transactions_are_extracted_locally_for_review(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.statement_extractor = StatementDocumentExtractor(
        DocumentExtractor(UnexpectedOcr())
    )
    fixture = (
        Path(__file__).parent / "fixtures" / "statements" / "synthetic_transaction_pdf_text.txt"
    )
    pdf = make_text_pdf(fixture.read_text(encoding="utf-8").splitlines())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                session.add(Category(workspace_id=None, name="Income", kind="income"))
                session.commit()
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("checking.pdf", pdf, "application/pdf")},
                follow_redirects=False,
            )
            review = await client.get(uploaded.headers["location"])
        with factory() as session:
            job = session.scalar(select(ImportJob))
            transaction_count = session.scalar(select(func.count(Transaction.id)))
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert "/review" in uploaded.headers["location"]
    assert review.status_code == 200
    assert "Example Market" in review.text
    assert "-12.34" in review.text
    assert "Payroll" in review.text
    assert "2500.00" in review.text
    assert job is not None and job.status == "reviewing"
    assert transaction_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content_type", "body"),
    [
        ("checking.pdf", "application/pdf", b"%PDF-this-is-malformed"),
        ("checking.pdf", "text/plain", b"private"),
    ],
)
async def test_invalid_pdf_upload_returns_safe_error_without_private_state(
    tmp_path: Path, filename: str, content_type: str, body: bytes
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
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": (filename, body, content_type)},
            )
        with factory() as session:
            jobs = session.scalar(select(func.count(ImportJob.id)))
            uploads = session.scalar(select(func.count(UploadedFile.id)))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert jobs == 0
    assert uploads == 0
    assert list(tmp_path.rglob("*.pdf")) == []
