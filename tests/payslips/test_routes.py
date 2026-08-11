import re
import threading
from datetime import datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import (
    IncomeRecord,
    Payslip,
    UploadedFile,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.payslips.extraction import ExtractedText
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token

PDF_BYTES = b"%PDF-synthetic-route-placeholder"
SYNTHETIC_TEXT = """
Employer: Northstar Bicycle Works
Pay Period: 2026-07-01 - 2026-07-15
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Taxes: $900.00
Deductions: $400.00
Net Pay: $3,700.00
"""


class RouteFakeExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(text=SYNTHETIC_TEXT, method="embedded_text")


class ThreadRecordingExtractor(RouteFakeExtractor):
    def __init__(self) -> None:
        self.thread_id: int | None = None

    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        self.thread_id = threading.get_ident()
        return super().extract(data, suffix)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_new_payslip_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/payslips/new", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_payslip_upload_requires_csrf_before_file_or_database_mutation(
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
            response = await client.post(
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "delete_after_import"},
                files={"payslip_file": ("synthetic.pdf", PDF_BYTES, "application/pdf")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(Payslip.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 403
    assert list(tmp_path.rglob("*.pdf")) == []


@pytest.mark.anyio
async def test_oversized_payslip_request_is_rejected_before_auth_or_form_parsing(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    oversized_body = b"x" * (application.state.settings.max_payslip_upload_bytes + 64 * 1024 + 1)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/workspaces/1/payslips",
                content=oversized_body,
                headers={"content-type": "multipart/form-data; boundary=synthetic"},
            )
        with factory() as session:
            assert session.scalar(select(func.count(Payslip.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 413
    assert response.text == "Payslip upload is too large."
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "filename, content_type",
    [
        ("synthetic.txt", "text/plain"),
        ("synthetic.pdf", "text/plain"),
        ("synthetic.pdf", ""),
    ],
)
async def test_invalid_upload_type_creates_no_private_file_or_record(
    tmp_path: Path, filename: str, content_type: str
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
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": (filename, PDF_BYTES, content_type)},
            )
        with factory() as session:
            assert session.scalar(select(func.count(Payslip.id))) == 0
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert "Choose a PDF, PNG, or JPEG payslip" in response.text
    assert list(tmp_path.rglob("*.pdf")) == []


@pytest.mark.anyio
async def test_nonmember_cannot_open_payslip_or_income_pages(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                stranger = User(google_sub="payslip-stranger", email="stranger@example.test")
                private_workspace = Workspace(
                    name="Stranger private", is_personal=True, owner=stranger
                )
                session.add(private_workspace)
                session.commit()
                workspace_id = private_workspace.id
            upload_page = await client.get(f"/workspaces/{workspace_id}/payslips/new")
            income_page = await client.get(f"/workspaces/{workspace_id}/income")
    finally:
        engine.dispose()

    assert upload_page.status_code == 404
    assert income_page.status_code == 404


@pytest.mark.anyio
async def test_foreign_payslip_id_is_hidden_inside_an_authorized_workspace(
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
                current = session.scalar(
                    select(User).where(User.email == "import-route@example.com")
                )
                current_workspace = session.scalar(
                    select(Workspace)
                    .join(WorkspaceMembership)
                    .where(WorkspaceMembership.user_id == current.id)
                )
                other = Workspace(name="Shared other", is_personal=False, owner=current)
                session.add(other)
                session.flush()
                session.add(WorkspaceMembership(workspace_id=other.id, user_id=current.id))
                foreign_payslip = Payslip(workspace_id=other.id, review_status="pending")
                session.add(foreign_payslip)
                session.commit()
                assert current_workspace is not None
                current_workspace_id = current_workspace.id
                foreign_id = foreign_payslip.id
            review = await client.get(
                f"/workspaces/{current_workspace_id}/payslips/{foreign_id}/review"
            )
            confirm = await client.post(
                f"/workspaces/{current_workspace_id}/payslips/{foreign_id}/confirm",
                data={
                    "csrf_token": token,
                    "employer": "Synthetic",
                    "pay_date": "2026-07-20",
                    "gross_pay": "1.00",
                    "net_pay": "1.00",
                    "taxes": "0.00",
                    "deductions": "0.00",
                },
            )
        with factory() as session:
            assert session.scalar(select(func.count(IncomeRecord.id))) == 0
    finally:
        engine.dispose()

    assert review.status_code == 404
    assert confirm.status_code == 404


@pytest.mark.anyio
async def test_legacy_payslip_without_candidate_json_still_opens_review(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                payslip = Payslip(
                    workspace_id=workspace.id,
                    review_status="pending",
                    candidate_fields=None,
                )
                session.add(payslip)
                session.commit()
                location = f"/workspaces/{workspace.id}/payslips/{payslip.id}/review"
            response = await client.get(location)
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "Manual review" in response.text


@pytest.mark.anyio
async def test_valid_upload_shows_editable_review_and_requires_confirmation(
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
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": ("synthetic.pdf", PDF_BYTES, "application/pdf")},
                follow_redirects=False,
            )
            review = await client.get(uploaded.headers["location"])
        with factory() as session:
            assert session.scalar(select(func.count(IncomeRecord.id))) == 0
            pending = session.scalar(select(Payslip))
            assert pending is not None and pending.review_status == "pending"
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert review.status_code == 200
    assert re.search(r'name="employer"[^>]*value="Northstar Bicycle Works"', review.text)
    assert re.search(r'name="pay_period_start"[^>]*value="2026-07-01"', review.text)
    assert re.search(r'name="pay_date"[^>]*value="2026-07-20"', review.text)
    assert re.search(r'name="gross_pay"[^>]*value="5000.00"', review.text)
    assert re.search(r'name="net_pay"[^>]*value="3700.00"', review.text)
    assert "Nothing is saved as income until you confirm" in review.text


@pytest.mark.anyio
async def test_blocking_local_extraction_runs_off_the_async_event_loop(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    extractor = ThreadRecordingExtractor()
    application.state.payslip_extractor = extractor
    event_loop_thread = threading.get_ident()
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
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": ("synthetic.pdf", PDF_BYTES, "application/pdf")},
                follow_redirects=False,
            )
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert extractor.thread_id is not None
    assert extractor.thread_id != event_loop_thread


@pytest.mark.anyio
async def test_confirmation_persists_edited_form_once_and_redirects_to_income(
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
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": ("synthetic.pdf", PDF_BYTES, "application/pdf")},
                follow_redirects=False,
            )
            confirmation = await client.post(
                uploaded.headers["location"].replace("/review", "/confirm"),
                data={
                    "csrf_token": token,
                    "employer": "Edited Northstar Works",
                    "pay_period_start": "2026-07-02",
                    "pay_period_end": "2026-07-16",
                    "pay_date": "2026-07-21",
                    "gross_pay": "5100.25",
                    "net_pay": "3800.10",
                    "taxes": "900.00",
                    "deductions": "400.15",
                },
                follow_redirects=False,
            )
        with factory() as session:
            record = session.scalar(select(IncomeRecord))
            assert record is not None
            record_values = (
                record.employer,
                record.gross_pay_cents,
                record.net_pay_cents,
            )
            assert session.scalar(select(func.count(IncomeRecord.id))) == 1
            assert session.scalar(select(func.count()).select_from(Payslip)) == 1
    finally:
        engine.dispose()

    assert confirmation.status_code == 303
    assert confirmation.headers["location"] == f"/workspaces/{workspace_id}/income"
    assert record_values == ("Edited Northstar Works", 510025, 380010)


@pytest.mark.anyio
async def test_invalid_confirmation_redisplays_submitted_values_without_income(
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
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/payslips",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"payslip_file": ("synthetic.pdf", PDF_BYTES, "application/pdf")},
                follow_redirects=False,
            )
            response = await client.post(
                uploaded.headers["location"].replace("/review", "/confirm"),
                data={
                    "csrf_token": token,
                    "employer": "My corrected employer",
                    "pay_period_start": "2026-07-01",
                    "pay_period_end": "2026-07-15",
                    "pay_date": "2026-07-20",
                    "gross_pay": "not money",
                    "net_pay": "3700.00",
                    "taxes": "900.00",
                    "deductions": "400.00",
                },
            )
        with factory() as session:
            assert session.scalar(select(func.count(IncomeRecord.id))) == 0
            pending = session.scalar(select(Payslip))
            assert pending is not None and pending.review_status == "pending"
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert 'value="My corrected employer"' in response.text
    assert 'value="not money"' in response.text
    assert "non-negative amount with at most two decimal places" in response.text


@pytest.mark.anyio
async def test_income_page_shows_exact_workspace_totals_and_newest_records_first(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                stranger = User(google_sub="summary-other", email="summary-other@example.test")
                foreign_workspace = Workspace(
                    name="Foreign income", is_personal=True, owner=stranger
                )
                session.add(foreign_workspace)
                session.flush()
                session.add_all(
                    [
                        IncomeRecord(
                            workspace_id=workspace.id,
                            employer="Older Synthetic Employer",
                            pay_date=datetime(2026, 7, 20),
                            gross_pay_cents=500000,
                            net_pay_cents=370000,
                            taxes_cents=90000,
                            deductions_cents=40000,
                        ),
                        IncomeRecord(
                            workspace_id=workspace.id,
                            employer="Newer Synthetic Employer",
                            pay_date=datetime(2026, 8, 1),
                            gross_pay_cents=125050,
                            net_pay_cents=100025,
                            taxes_cents=20000,
                            deductions_cents=5025,
                        ),
                        IncomeRecord(
                            workspace_id=foreign_workspace.id,
                            employer="Private Foreign Employer",
                            pay_date=datetime(2026, 8, 2),
                            gross_pay_cents=999999,
                            net_pay_cents=888888,
                            taxes_cents=0,
                            deductions_cents=0,
                        ),
                        Payslip(
                            workspace_id=workspace.id,
                            review_status="pending",
                            candidate_fields={
                                "gross_pay_cents": 777777,
                                "net_pay_cents": 666666,
                            },
                        ),
                    ]
                )
                session.commit()
                workspace_id = workspace.id
            response = await client.get(f"/workspaces/{workspace_id}/income")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "$6,250.50" in response.text
    assert "$4,700.25" in response.text
    assert "2 confirmed payslips" in response.text
    assert response.text.index("Newer Synthetic Employer") < response.text.index(
        "Older Synthetic Employer"
    )
    assert "Private Foreign Employer" not in response.text
    assert "$7,777.77" not in response.text


@pytest.mark.anyio
async def test_empty_income_page_shows_zero_totals_and_cleanup_warning(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.get(f"/workspaces/{workspace_id}/income?cleanup_failed=1")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.text.count("$0.00") >= 2
    assert "No confirmed income records yet" in response.text
    assert "income was confirmed, but the private source file could not be deleted" in response.text
