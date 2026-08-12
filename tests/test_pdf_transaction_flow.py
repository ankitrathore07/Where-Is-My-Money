from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import Category, ImportJob, Transaction, Workspace
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
    pdf = make_text_pdf(
        [
            "Fictional Checking Statement",
            "08/01/2026 Example Market -$12.34 $1,250.00",
            "2026-08-02 Payroll $2,500.00 CR $3,750.00",
        ]
    )
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
