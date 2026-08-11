from io import BytesIO
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import func, select

from app.db.models import IncomeRecord, Transaction, Workspace
from app.payslips.extraction import DocumentExtractor
from tests.payslips.pdf_helpers import make_text_pdf
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token

FIXTURE = Path(__file__).parents[1] / "fixtures" / "payslips" / "synthetic_paystub_text.txt"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _confirmation_form(csrf: str) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "employer": "Northstar Bicycle Works",
        "pay_period_start": "2026-07-01",
        "pay_period_end": "2026-07-15",
        "pay_date": "2026-07-20",
        "gross_pay": "5000.00",
        "net_pay": "3700.00",
        "taxes": "900.00",
        "deductions": "400.00",
    }


@pytest.mark.anyio
async def test_text_pdf_requires_confirmation_and_updates_income_totals(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    pdf_bytes = make_text_pdf(FIXTURE.read_text(encoding="utf-8").splitlines())
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
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"payslip_file": ("synthetic.pdf", pdf_bytes, "application/pdf")},
                follow_redirects=False,
            )
            review = await client.get(uploaded.headers["location"])
            with factory() as session:
                assert session.scalar(select(func.count(IncomeRecord.id))) == 0
                assert session.scalar(select(func.count(Transaction.id))) == 0
            confirmed = await client.post(
                uploaded.headers["location"].replace("/review", "/confirm"),
                data=_confirmation_form(token),
                follow_redirects=False,
            )
            income_page = await client.get(confirmed.headers["location"])
        with factory() as session:
            assert session.scalar(select(func.count(IncomeRecord.id))) == 1
            assert session.scalar(select(func.count(Transaction.id))) == 0
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert review.status_code == 200
    assert "Embedded PDF text" in review.text
    assert "Nothing is saved as income until you confirm" in review.text
    assert confirmed.status_code == 303
    assert "$5,000.00" in income_page.text
    assert "$3,700.00" in income_page.text


class SyntheticOcr:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def extract_png(self, image_bytes: bytes) -> str:
        self.calls.append(image_bytes)
        return FIXTURE.read_text(encoding="utf-8")


def _scanned_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 1000), "white").save(output, format="PNG")
    return output.getvalue()


def _scanned_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "filename, content_type, source",
    [
        ("synthetic-scan.png", "image/png", _scanned_png),
        ("synthetic-scan.pdf", "application/pdf", _scanned_pdf),
    ],
)
async def test_scanned_payslip_requires_confirmation_and_updates_income_totals(
    tmp_path: Path,
    filename: str,
    content_type: str,
    source,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    ocr = SyntheticOcr()
    application.state.payslip_extractor = DocumentExtractor(ocr)
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
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": (filename, source(), content_type)},
                follow_redirects=False,
            )
            review = await client.get(uploaded.headers["location"])
            with factory() as session:
                assert session.scalar(select(func.count(IncomeRecord.id))) == 0
            confirmed = await client.post(
                uploaded.headers["location"].replace("/review", "/confirm"),
                data=_confirmation_form(token),
                follow_redirects=False,
            )
            income_page = await client.get(confirmed.headers["location"])
        with factory() as session:
            assert session.scalar(select(func.count(IncomeRecord.id))) == 1
            assert session.scalar(select(func.count(Transaction.id))) == 0
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert review.status_code == 200
    assert "Local OCR" in review.text
    assert len(ocr.calls) == 1
    assert ocr.calls[0].startswith(b"\x89PNG\r\n\x1a\n")
    assert confirmed.status_code == 303
    assert "$5,000.00" in income_page.text
    assert "$3,700.00" in income_page.text
