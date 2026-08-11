from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IncomeRecord, Payslip, UploadedFile, Workspace
from app.payslips.extraction import DocumentExtractor
from app.payslips.parsing import (
    PayslipCandidate,
    ReviewValues,
    extract_candidates,
    validate_review,
)
from app.payslips.storage import (
    PayslipStorageError,
    PayslipUploadStore,
    StoredPayslipUpload,
)

RETENTION_CHOICES = {"retain", "delete_after_import"}


class PayslipImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _as_datetime(value: date | None) -> datetime | None:
    return datetime.combine(value, time.min, tzinfo=UTC) if value is not None else None


def _confidence(candidate: PayslipCandidate, method: str) -> float:
    values = candidate.to_json().values()
    coverage = sum(value is not None for value in values) / 8
    method_weight = 1.0 if method == "embedded_text" else 0.75
    return round(coverage * method_weight, 2)


def _cleanup_failed_source(
    session: Session,
    store: PayslipUploadStore,
    stored: StoredPayslipUpload | None,
) -> None:
    session.rollback()
    if stored is None:
        return
    try:
        store.delete(stored.storage_key)
    except OSError as exc:
        raise PayslipImportError(
            "cleanup_failed",
            "The invalid private payslip source could not be removed.",
        ) from exc


def create_payslip_import(
    session: Session,
    store: PayslipUploadStore,
    extractor: DocumentExtractor,
    workspace: Workspace,
    stream: BinaryIO,
    suffix: str,
    retention_choice: str,
) -> Payslip:
    """Store and extract review candidates without creating confirmed income."""
    if retention_choice not in RETENTION_CHOICES:
        raise PayslipImportError(
            "invalid_retention", "Choose a valid private-file retention choice."
        )

    stored: StoredPayslipUpload | None = None
    try:
        stored = store.save(workspace.id, suffix, stream)
        extracted = extractor.extract(store.read(stored.storage_key), suffix)
        candidate = extract_candidates(extracted.text)
        candidate_fields = candidate.to_json()
        candidate_fields["extraction_method"] = extracted.method
        canonical_type = "jpg" if suffix.casefold() == ".jpeg" else suffix.casefold().lstrip(".")
        uploaded_file = UploadedFile(
            workspace_id=workspace.id,
            file_type=canonical_type,
            storage_path=stored.storage_key,
            checksum=stored.checksum,
            size_bytes=stored.size_bytes,
            retention_choice=retention_choice,
        )
        payslip = Payslip(
            workspace_id=workspace.id,
            uploaded_file=uploaded_file,
            employer=candidate.employer,
            pay_period_start=_as_datetime(candidate.pay_period_start),
            pay_period_end=_as_datetime(candidate.pay_period_end),
            pay_date=_as_datetime(candidate.pay_date),
            candidate_fields=candidate_fields,
            confidence=_confidence(candidate, extracted.method),
            review_status="pending",
        )
        session.add(payslip)
        session.commit()
        return payslip
    except Exception:
        _cleanup_failed_source(session, store, stored)
        raise


@dataclass(frozen=True)
class ConfirmationResult:
    record: IncomeRecord
    cleanup_failed: bool
    already_confirmed: bool


@dataclass(frozen=True)
class IncomeSummary:
    records: tuple[IncomeRecord, ...]
    record_count: int
    gross_pay_cents: int
    net_pay_cents: int


def get_workspace_payslip(session: Session, workspace_id: int, payslip_id: int) -> Payslip | None:
    """Load a payslip only through its workspace boundary."""
    return session.scalar(
        select(Payslip).where(
            Payslip.id == payslip_id,
            Payslip.workspace_id == workspace_id,
        )
    )


def _confirmed_candidate_fields(payslip: Payslip, values: ReviewValues) -> dict[str, object]:
    existing = payslip.candidate_fields if isinstance(payslip.candidate_fields, dict) else {}
    return {
        "employer": values.employer,
        "pay_period_start": (
            values.pay_period_start.isoformat() if values.pay_period_start else None
        ),
        "pay_period_end": values.pay_period_end.isoformat() if values.pay_period_end else None,
        "pay_date": values.pay_date.isoformat(),
        "gross_pay_cents": values.gross_pay_cents,
        "net_pay_cents": values.net_pay_cents,
        "taxes_cents": values.taxes_cents,
        "deductions_cents": values.deductions_cents,
        "extraction_method": existing.get("extraction_method"),
    }


def confirm_payslip(
    session: Session,
    store: PayslipUploadStore,
    payslip: Payslip,
    form: Mapping[str, str],
) -> ConfirmationResult:
    """Persist one explicitly reviewed income record, then honor source retention."""
    payslip_id = payslip.id
    workspace_id = payslip.workspace_id
    existing = session.scalar(
        select(IncomeRecord).where(
            IncomeRecord.payslip_id == payslip_id,
            IncomeRecord.workspace_id == workspace_id,
        )
    )
    if existing is not None:
        return ConfirmationResult(
            record=existing,
            cleanup_failed=payslip.review_status == "confirmed_cleanup_failed",
            already_confirmed=True,
        )
    if payslip.review_status != "pending":
        raise PayslipImportError("not_pending", "This payslip is not waiting for confirmation.")

    values = validate_review(form)
    record = IncomeRecord(
        workspace_id=payslip.workspace_id,
        payslip_id=payslip.id,
        employer=values.employer,
        pay_date=_as_datetime(values.pay_date),
        gross_pay_cents=values.gross_pay_cents,
        net_pay_cents=values.net_pay_cents,
        taxes_cents=values.taxes_cents,
        deductions_cents=values.deductions_cents,
    )
    payslip.employer = values.employer
    payslip.pay_period_start = _as_datetime(values.pay_period_start)
    payslip.pay_period_end = _as_datetime(values.pay_period_end)
    payslip.pay_date = _as_datetime(values.pay_date)
    payslip.candidate_fields = _confirmed_candidate_fields(payslip, values)
    payslip.review_status = "confirmed"
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(IncomeRecord).where(
                IncomeRecord.payslip_id == payslip_id,
                IncomeRecord.workspace_id == workspace_id,
            )
        )
        if existing is None:
            raise
        confirmed_payslip = session.get(Payslip, payslip_id)
        return ConfirmationResult(
            record=existing,
            cleanup_failed=(
                confirmed_payslip is not None
                and confirmed_payslip.review_status == "confirmed_cleanup_failed"
            ),
            already_confirmed=True,
        )

    cleanup_failed = False
    uploaded_file = payslip.uploaded_file
    if (
        uploaded_file is not None
        and uploaded_file.retention_choice == "delete_after_import"
        and not uploaded_file.deleted
    ):
        try:
            store.delete(uploaded_file.storage_path)
        except (OSError, PayslipStorageError):
            cleanup_failed = True
            payslip.review_status = "confirmed_cleanup_failed"
        else:
            uploaded_file.deleted = True
        session.commit()

    return ConfirmationResult(
        record=record,
        cleanup_failed=cleanup_failed,
        already_confirmed=False,
    )


def get_income_summary(session: Session, workspace_id: int) -> IncomeSummary:
    """Return confirmed income rows and exact gross/net totals for one workspace."""
    record_count, gross_pay_cents, net_pay_cents = session.execute(
        select(
            func.count(IncomeRecord.id),
            func.coalesce(func.sum(IncomeRecord.gross_pay_cents), 0),
            func.coalesce(func.sum(IncomeRecord.net_pay_cents), 0),
        ).where(IncomeRecord.workspace_id == workspace_id)
    ).one()
    records = tuple(
        session.scalars(
            select(IncomeRecord)
            .where(IncomeRecord.workspace_id == workspace_id)
            .order_by(IncomeRecord.pay_date.desc(), IncomeRecord.id.desc())
        )
    )
    return IncomeSummary(
        records=records,
        record_count=int(record_count),
        gross_pay_cents=int(gross_pay_cents),
        net_pay_cents=int(net_pay_cents),
    )
