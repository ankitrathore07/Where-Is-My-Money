from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, IncomeRecord, Payslip, Transaction, UploadedFile, User, Workspace
from app.payslips.extraction import DocumentExtractionError, ExtractedText
from app.payslips.parsing import ReviewValidationError
from app.payslips.service import (
    PayslipImportError,
    confirm_payslip,
    create_payslip_import,
    get_income_summary,
    get_workspace_payslip,
)
from app.payslips.storage import PayslipStorageError, PayslipUploadStore

SYNTHETIC_TEXT = """
Employer: Northstar Bicycle Works
Pay Period: 2026-07-01 - 2026-07-15
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Taxes: $900.00
Deductions: $400.00
Net Pay: $3,700.00
"""


class FakeExtractor:
    def __init__(self, text: str = SYNTHETIC_TEXT, method: str = "embedded_text") -> None:
        self.text = text
        self.method = method

    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == b"synthetic-private-source"
        assert suffix == ".pdf"
        return ExtractedText(text=self.text, method=self.method)  # type: ignore[arg-type]


class FailingExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        raise DocumentExtractionError("invalid_pdf", "Choose a valid PDF payslip.")


def test_pending_import_stores_candidates_without_creating_income(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)

    payslip = create_payslip_import(
        session,
        store,
        FakeExtractor(),
        workspace,
        BytesIO(b"synthetic-private-source"),
        ".pdf",
        "retain",
    )

    assert payslip.review_status == "pending"
    assert payslip.employer == "Northstar Bicycle Works"
    assert payslip.candidate_fields == {
        "employer": "Northstar Bicycle Works",
        "pay_period_start": "2026-07-01",
        "pay_period_end": "2026-07-15",
        "pay_date": "2026-07-20",
        "gross_pay_cents": 500000,
        "net_pay_cents": 370000,
        "taxes_cents": 90000,
        "deductions_cents": 40000,
        "extraction_method": "embedded_text",
    }
    assert payslip.confidence == 1.0
    assert payslip.uploaded_file is not None
    assert payslip.uploaded_file.retention_choice == "retain"
    assert store.read(payslip.uploaded_file.storage_path) == b"synthetic-private-source"
    assert session.scalar(select(func.count(IncomeRecord.id))) == 0


@pytest.mark.parametrize("retention", ["", "delete", "forever"])
def test_invalid_retention_is_rejected_before_file_or_database_write(
    session: Session, workspace, tmp_path: Path, retention: str
) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(PayslipImportError, match="retention choice") as error:
        create_payslip_import(
            session,
            store,
            FakeExtractor(),
            workspace,
            BytesIO(b"synthetic-private-source"),
            ".pdf",
            retention,
        )

    assert error.value.code == "invalid_retention"
    assert list(tmp_path.rglob("*.*")) == []
    assert session.scalar(select(func.count(Payslip.id))) == 0
    assert session.scalar(select(func.count(UploadedFile.id))) == 0


def test_extraction_failure_deletes_source_and_creates_no_records(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(DocumentExtractionError, match="valid PDF"):
        create_payslip_import(
            session,
            store,
            FailingExtractor(),
            workspace,
            BytesIO(b"synthetic-private-source"),
            ".pdf",
            "delete_after_import",
        )

    assert list(tmp_path.rglob("*.*")) == []
    assert session.scalar(select(func.count(Payslip.id))) == 0
    assert session.scalar(select(func.count(UploadedFile.id))) == 0


def test_storage_failure_creates_no_database_records(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path, max_bytes=4)

    with pytest.raises(PayslipStorageError, match="at most 4 bytes"):
        create_payslip_import(
            session,
            store,
            FakeExtractor(),
            workspace,
            BytesIO(b"synthetic-private-source"),
            ".pdf",
            "retain",
        )

    assert session.scalar(select(func.count(Payslip.id))) == 0
    assert session.scalar(select(func.count(UploadedFile.id))) == 0


def _pending_payslip(
    session: Session,
    workspace,
    store: PayslipUploadStore,
    retention_choice: str = "retain",
) -> Payslip:
    return create_payslip_import(
        session,
        store,
        FakeExtractor(),
        workspace,
        BytesIO(b"synthetic-private-source"),
        ".pdf",
        retention_choice,
    )


def _valid_review(**overrides: str) -> dict[str, str]:
    values = {
        "employer": "Edited Northstar Works",
        "pay_period_start": "2026-07-02",
        "pay_period_end": "2026-07-16",
        "pay_date": "2026-07-21",
        "gross_pay": "5,100.25",
        "net_pay": "3,800.10",
        "taxes": "900.00",
        "deductions": "400.15",
    }
    values.update(overrides)
    return values


def test_confirmation_uses_edited_values_and_never_creates_a_transaction(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store)
    assert session.scalar(select(func.count(Transaction.id))) == 0

    result = confirm_payslip(session, store, payslip, _valid_review())

    assert result.already_confirmed is False
    assert result.cleanup_failed is False
    assert result.record.workspace_id == workspace.id
    assert result.record.payslip_id == payslip.id
    assert result.record.employer == "Edited Northstar Works"
    assert result.record.pay_date.date().isoformat() == "2026-07-21"
    assert result.record.gross_pay_cents == 510025
    assert result.record.net_pay_cents == 380010
    assert result.record.taxes_cents == 90000
    assert result.record.deductions_cents == 40015
    assert payslip.review_status == "confirmed"
    assert payslip.pay_period_start.date().isoformat() == "2026-07-02"
    assert payslip.pay_period_end.date().isoformat() == "2026-07-16"
    assert session.scalar(select(func.count(Transaction.id))) == 0


def test_invalid_confirmation_keeps_payslip_pending_and_creates_no_income(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store)

    with pytest.raises(ReviewValidationError) as error:
        confirm_payslip(session, store, payslip, _valid_review(gross_pay="-1.00"))

    assert "gross_pay" in error.value.field_errors
    assert payslip.review_status == "pending"
    assert session.scalar(select(func.count(IncomeRecord.id))) == 0


def test_second_confirmation_is_idempotent(session: Session, workspace, tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store)
    first = confirm_payslip(session, store, payslip, _valid_review())

    second = confirm_payslip(
        session, store, payslip, _valid_review(gross_pay="9999.00", net_pay="9999.00")
    )

    assert second.already_confirmed is True
    assert second.record.id == first.record.id
    assert second.record.gross_pay_cents == 510025
    assert session.scalar(select(func.count(IncomeRecord.id))) == 1


def test_two_database_sessions_confirming_together_create_one_income_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "concurrent-confirmation.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as setup_session:
        owner = User(google_sub="concurrent-owner", email="concurrent@example.test")
        workspace = Workspace(name="Concurrent", is_personal=True, owner=owner)
        payslip = Payslip(
            workspace=workspace,
            review_status="pending",
            candidate_fields={"extraction_method": "embedded_text"},
        )
        setup_session.add(payslip)
        setup_session.commit()
        payslip_id = payslip.id

    import app.payslips.service as payslip_service

    real_validate_review = payslip_service.validate_review
    ready_to_insert = Barrier(2)

    def synchronized_validation(form):
        values = real_validate_review(form)
        ready_to_insert.wait(timeout=5)
        return values

    monkeypatch.setattr(payslip_service, "validate_review", synchronized_validation)
    store = PayslipUploadStore(tmp_path / "uploads")

    def confirm_in_new_session() -> tuple[int, bool]:
        with factory() as worker_session:
            worker_payslip = worker_session.get(Payslip, payslip_id)
            assert worker_payslip is not None
            result = confirm_payslip(worker_session, store, worker_payslip, _valid_review())
            return result.record.id, result.already_confirmed

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: confirm_in_new_session(), range(2)))
        with factory() as check_session:
            record_count = check_session.scalar(select(func.count(IncomeRecord.id)))
    finally:
        engine.dispose()

    assert len({record_id for record_id, _ in results}) == 1
    assert sorted(already_confirmed for _, already_confirmed in results) == [False, True]
    assert record_count == 1


def test_delete_after_confirmation_removes_private_source(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store, "delete_after_import")
    assert payslip.uploaded_file is not None
    storage_key = payslip.uploaded_file.storage_path

    result = confirm_payslip(session, store, payslip, _valid_review())

    assert result.cleanup_failed is False
    assert payslip.uploaded_file.deleted is True
    with pytest.raises(PayslipStorageError, match="missing"):
        store.read(storage_key)


def test_retain_choice_keeps_private_source_after_confirmation(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = PayslipUploadStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store, "retain")
    assert payslip.uploaded_file is not None

    confirm_payslip(session, store, payslip, _valid_review())

    assert payslip.uploaded_file.deleted is False
    assert store.read(payslip.uploaded_file.storage_path) == b"synthetic-private-source"


class CleanupFailingStore(PayslipUploadStore):
    def delete(self, storage_key: str) -> None:
        raise OSError("synthetic local cleanup failure")


def test_cleanup_failure_keeps_confirmed_income_and_truthful_status(
    session: Session, workspace, tmp_path: Path
) -> None:
    store = CleanupFailingStore(tmp_path)
    payslip = _pending_payslip(session, workspace, store, "delete_after_import")

    result = confirm_payslip(session, store, payslip, _valid_review())

    assert result.cleanup_failed is True
    assert payslip.review_status == "confirmed_cleanup_failed"
    assert result.record.id is not None
    assert session.scalar(select(func.count(IncomeRecord.id))) == 1
    assert payslip.uploaded_file is not None
    assert payslip.uploaded_file.deleted is False


def test_workspace_payslip_lookup_never_returns_another_workspaces_record(
    session: Session, workspace, other_workspace, tmp_path: Path
) -> None:
    payslip = _pending_payslip(session, workspace, PayslipUploadStore(tmp_path))

    assert get_workspace_payslip(session, workspace.id, payslip.id) is payslip
    assert get_workspace_payslip(session, other_workspace.id, payslip.id) is None


def test_income_summary_uses_only_confirmed_records_in_target_workspace(
    session: Session, workspace, other_workspace
) -> None:
    older = IncomeRecord(
        workspace_id=workspace.id,
        employer="Northstar Bicycle Works",
        pay_date=datetime(2026, 7, 20),
        gross_pay_cents=500000,
        net_pay_cents=370000,
        taxes_cents=90000,
        deductions_cents=40000,
    )
    newer = IncomeRecord(
        workspace_id=workspace.id,
        employer="Synthetic Side Project",
        pay_date=datetime(2026, 8, 1),
        gross_pay_cents=125050,
        net_pay_cents=100025,
        taxes_cents=20000,
        deductions_cents=5025,
    )
    foreign = IncomeRecord(
        workspace_id=other_workspace.id,
        employer="Other Workspace Employer",
        pay_date=datetime(2026, 8, 2),
        gross_pay_cents=999999,
        net_pay_cents=888888,
        taxes_cents=0,
        deductions_cents=0,
    )
    pending = Payslip(
        workspace_id=workspace.id,
        review_status="pending",
        candidate_fields={"gross_pay_cents": 777777, "net_pay_cents": 666666},
    )
    session.add_all([older, newer, foreign, pending])
    session.commit()

    summary = get_income_summary(session, workspace.id)

    assert summary.record_count == 2
    assert summary.gross_pay_cents == 625050
    assert summary.net_pay_cents == 470025
    assert [record.id for record in summary.records] == [newer.id, older.id]
    assert all(record.workspace_id == workspace.id for record in summary.records)


def test_empty_income_summary_reports_literal_zeros(session: Session, workspace) -> None:
    summary = get_income_summary(session, workspace.id)

    assert summary.records == ()
    assert summary.record_count == 0
    assert summary.gross_pay_cents == 0
    assert summary.net_pay_cents == 0
