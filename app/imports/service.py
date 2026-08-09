from collections.abc import Mapping
from dataclasses import dataclass
from typing import BinaryIO, Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import ImportJob, UploadedFile, Workspace
from app.imports.duplicates import find_existing_fingerprints, fingerprint_transactions
from app.imports.mapping import mapping_from_json, validate_mapping
from app.imports.normalization import RowValidationError, normalize_source_row
from app.imports.parser import parse_csv_bytes
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.imports.types import (
    ColumnMapping,
    CsvDocument,
    ImportReview,
    NormalizedTransaction,
    ReviewRow,
)

ACTIVE_STATUSES = {"awaiting_mapping", "reviewing"}
COMMITTED_STATUSES = {"committed", "committed_cleanup_failed"}
RETENTION_CHOICES = {"delete_after_import", "retain"}
UploadResultKind = Literal["created", "resume", "already_committed"]


class ImportStateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadResult:
    kind: UploadResultKind
    job: ImportJob


def get_workspace_import(session: Session, workspace_id: int, import_id: int) -> ImportJob | None:
    """Load an import only through its authorized workspace boundary."""
    return session.scalar(
        select(ImportJob).where(
            ImportJob.id == import_id,
            ImportJob.workspace_id == workspace_id,
        )
    )


def _matching_import(session: Session, workspace_id: int, checksum: str) -> ImportJob | None:
    return session.scalar(
        select(ImportJob)
        .where(
            ImportJob.workspace_id == workspace_id,
            ImportJob.source_checksum == checksum,
            ImportJob.status.in_(ACTIVE_STATUSES | COMMITTED_STATUSES),
        )
        .order_by(ImportJob.id.desc())
    )


def create_csv_import(
    session: Session,
    store: LocalUploadStore,
    workspace: Workspace,
    upload: BinaryIO,
    retention_choice: str,
) -> UploadResult:
    """Validate a private CSV source and create or resume its import job."""
    if retention_choice not in RETENTION_CHOICES:
        raise ImportStateError(
            "invalid_retention", "Choose whether to delete or retain the source file."
        )

    saved = store.save(workspace.id, upload)
    try:
        parse_csv_bytes(store.read(saved.storage_key))
        existing = _matching_import(session, workspace.id, saved.checksum)
        if existing is not None:
            store.delete(saved.storage_key)
            kind: UploadResultKind = (
                "already_committed" if existing.status in COMMITTED_STATUSES else "resume"
            )
            return UploadResult(kind, existing)

        uploaded_file = UploadedFile(
            workspace_id=workspace.id,
            file_type="csv",
            storage_path=saved.storage_key,
            checksum=saved.checksum,
            size_bytes=saved.size_bytes,
            retention_choice=retention_choice,
            deleted=False,
        )
        job = ImportJob(
            workspace_id=workspace.id,
            uploaded_file=uploaded_file,
            status="awaiting_mapping",
            source_checksum=saved.checksum,
        )
        session.add(job)
        session.commit()
        return UploadResult("created", job)
    except Exception:
        session.rollback()
        store.delete(saved.storage_key)
        raise


def cancel_import(session: Session, store: LocalUploadStore, job: ImportJob) -> ImportJob:
    """Cancel an uncommitted job and truthfully record source cleanup."""
    if job.status not in ACTIVE_STATUSES:
        raise ImportStateError("cannot_cancel", "Only an uncommitted import can be canceled.")

    uploaded_file = job.uploaded_file
    if uploaded_file is None or uploaded_file.deleted:
        job.status = "canceled"
        job.validation_errors = None
        session.commit()
        return job

    try:
        store.delete(uploaded_file.storage_path)
    except (OSError, UploadStorageError):
        job.status = "canceled_cleanup_failed"
        job.validation_errors = {"cleanup": "delete_failed"}
    else:
        uploaded_file.deleted = True
        job.status = "canceled"
        job.validation_errors = None
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    return job


def _source_document(store: LocalUploadStore, job: ImportJob) -> CsvDocument:
    uploaded_file = job.uploaded_file
    if uploaded_file is None or uploaded_file.deleted:
        raise ImportStateError("source_missing", "The private source file is missing.")
    try:
        return parse_csv_bytes(store.read(uploaded_file.storage_path))
    except UploadStorageError as exc:
        raise ImportStateError("source_missing", "The private source file is missing.") from exc


def save_mapping(
    session: Session,
    store: LocalUploadStore,
    job: ImportJob,
    form: Mapping[str, object],
) -> ColumnMapping:
    """Validate mapping fields against the job's exact private source headers."""
    if job.status not in ACTIVE_STATUSES:
        raise ImportStateError("mapping_not_editable", "This import can no longer be mapped.")
    document = _source_document(store, job)
    mapping = validate_mapping(document.headers, form)
    job.column_mapping = mapping.to_json()
    job.validation_errors = None
    job.status = "reviewing"
    session.commit()
    return mapping


def _format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _raw_amount(row_values: Mapping[str, str], mapping: ColumnMapping) -> str:
    if mapping.amount_mode == "single":
        assert mapping.amount_column is not None
        return row_values.get(mapping.amount_column, "")
    assert mapping.debit_column is not None
    assert mapping.credit_column is not None
    debit = row_values.get(mapping.debit_column, "").strip()
    credit = row_values.get(mapping.credit_column, "").strip()
    if debit and credit:
        return f"{debit} / {credit}"
    return f"-{debit}" if debit else credit


def build_review(session: Session, store: LocalUploadStore, job: ImportJob) -> ImportReview:
    """Reparse a mapped source into editable review rows without writing data."""
    if job.status != "reviewing":
        raise ImportStateError("not_ready_for_review", "Map the CSV before reviewing it.")
    document = _source_document(store, job)
    if not isinstance(job.column_mapping, dict):
        raise ImportStateError("mapping_missing", "Map the CSV before reviewing it.")
    mapping = mapping_from_json(document.headers, job.column_mapping)

    normalized_by_row: dict[int, NormalizedTransaction] = {}
    errors_by_row: dict[int, dict[str, str]] = {}
    for source_row in document.rows:
        try:
            normalized_by_row[source_row.row_number] = normalize_source_row(source_row, mapping)
        except RowValidationError as exc:
            errors_by_row[source_row.row_number] = exc.field_errors

    normalized_rows = tuple(normalized_by_row.values())
    fingerprinted = fingerprint_transactions(normalized_rows)
    fingerprints_by_row = {item.transaction.row_number: item.fingerprint for item in fingerprinted}
    existing = find_existing_fingerprints(
        session, job.workspace_id, set(fingerprints_by_row.values())
    )

    review_rows: list[ReviewRow] = []
    for source_row in document.rows:
        normalized = normalized_by_row.get(source_row.row_number)
        fingerprint = fingerprints_by_row.get(source_row.row_number)
        duplicate = fingerprint in existing if fingerprint is not None else False
        if normalized is not None:
            review_rows.append(
                ReviewRow(
                    row_number=source_row.row_number,
                    date_value=normalized.transaction_date.isoformat(),
                    description_value=normalized.description,
                    amount_value=_format_cents(normalized.amount_cents),
                    normalized=normalized,
                    fingerprint=fingerprint,
                    duplicate=duplicate,
                    included=not duplicate,
                    field_errors={},
                )
            )
        else:
            review_rows.append(
                ReviewRow(
                    row_number=source_row.row_number,
                    date_value=source_row.values.get(mapping.date_column, ""),
                    description_value=source_row.values.get(mapping.description_column, ""),
                    amount_value=_raw_amount(source_row.values, mapping),
                    normalized=None,
                    fingerprint=None,
                    duplicate=False,
                    included=True,
                    field_errors=errors_by_row[source_row.row_number],
                )
            )

    return ImportReview(
        rows=tuple(review_rows),
        total_rows=len(review_rows),
        valid_rows=len(normalized_rows),
        invalid_rows=len(errors_by_row),
        duplicate_rows=sum(row.duplicate for row in review_rows),
    )
