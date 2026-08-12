from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.categorization.service import categorize_candidate
from app.categorization.types import CategorizationSource
from app.db.models import Category, ImportJob, Transaction, UploadedFile, Workspace
from app.imports.document_parser import parse_transaction_statement_text
from app.imports.duplicates import find_existing_fingerprints, fingerprint_transactions
from app.imports.mapping import mapping_from_json, validate_mapping
from app.imports.normalization import (
    RowValidationError,
    normalize_review_edit,
    normalize_source_row,
)
from app.imports.parser import parse_csv_bytes
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.imports.types import (
    ColumnMapping,
    CsvDocument,
    ImportReview,
    NormalizedTransaction,
    ReviewRow,
    RowEdit,
)

ACTIVE_STATUSES = {"awaiting_mapping", "reviewing"}
COMMITTED_STATUSES = {"committed", "committed_cleanup_failed"}
RETENTION_CHOICES = {"delete_after_import", "retain"}
UploadResultKind = Literal["created", "resume", "already_committed"]


class TransactionSourceText(Protocol):
    text: str


class TransactionSourceExtractor(Protocol):
    def extract(self, data: bytes, suffix: str) -> TransactionSourceText: ...


class ImportStateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UploadResult:
    kind: UploadResultKind
    job: ImportJob


class ReviewValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        row_errors: dict[int, dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.row_errors = row_errors or {}


@dataclass(frozen=True)
class CommitResult:
    job: ImportJob
    inserted_count: int
    duplicate_count: int
    excluded_count: int
    cleanup_failed: bool


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


def create_transaction_import(
    session: Session,
    store: LocalUploadStore,
    extractor: TransactionSourceExtractor,
    workspace: Workspace,
    filename: str,
    media_type: str,
    upload: BinaryIO,
    retention_choice: str,
) -> UploadResult:
    """Create a reviewed CSV or locally extracted PDF transaction import."""
    suffix = Path(filename).suffix.casefold()
    if suffix == ".csv":
        return create_csv_import(session, store, workspace, upload, retention_choice)
    if suffix != ".pdf" or media_type.casefold() != "application/pdf":
        raise ImportStateError(
            "unsupported_file_type", "Choose a CSV or PDF transaction statement."
        )
    if retention_choice not in RETENTION_CHOICES:
        raise ImportStateError(
            "invalid_retention", "Choose whether to delete or retain the source file."
        )

    saved = store.save(workspace.id, upload, suffix)
    try:
        extracted = extractor.extract(store.read(saved.storage_key), suffix)
        parse_transaction_statement_text(extracted.text)
        existing = _matching_import(session, workspace.id, saved.checksum)
        if existing is not None:
            store.delete(saved.storage_key)
            kind: UploadResultKind = (
                "already_committed" if existing.status in COMMITTED_STATUSES else "resume"
            )
            return UploadResult(kind, existing)

        mapping = ColumnMapping(
            date_column="Date",
            description_column="Description",
            amount_mode="single",
            amount_column="Amount",
            debit_column=None,
            credit_column=None,
            date_format="iso",
            amount_sign="as_is",
        )
        uploaded_file = UploadedFile(
            workspace_id=workspace.id,
            file_type="transaction_pdf",
            storage_path=saved.storage_key,
            checksum=saved.checksum,
            size_bytes=saved.size_bytes,
            retention_choice=retention_choice,
            deleted=False,
        )
        job = ImportJob(
            workspace_id=workspace.id,
            uploaded_file=uploaded_file,
            status="reviewing",
            column_mapping=mapping.to_json(),
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


def load_source_document(
    store: LocalUploadStore,
    job: ImportJob,
    extractor: TransactionSourceExtractor | None = None,
) -> CsvDocument:
    uploaded_file = job.uploaded_file
    if uploaded_file is None or uploaded_file.deleted:
        raise ImportStateError("source_missing", "The private source file is missing.")
    try:
        data = store.read(uploaded_file.storage_path)
    except UploadStorageError as exc:
        raise ImportStateError("source_missing", "The private source file is missing.") from exc
    suffix = Path(uploaded_file.storage_path).suffix.casefold()
    if suffix == ".csv":
        return parse_csv_bytes(data)
    if suffix == ".pdf" and extractor is not None:
        return parse_transaction_statement_text(extractor.extract(data, suffix).text)
    raise ImportStateError("source_unreadable", "The private transaction statement cannot be read.")


def save_mapping(
    session: Session,
    store: LocalUploadStore,
    job: ImportJob,
    form: Mapping[str, object],
    extractor: TransactionSourceExtractor | None = None,
) -> ColumnMapping:
    """Validate mapping fields against the job's exact private source headers."""
    if job.status not in ACTIVE_STATUSES:
        raise ImportStateError("mapping_not_editable", "This import can no longer be mapped.")
    document = load_source_document(store, job, extractor)
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


def build_review(
    session: Session,
    store: LocalUploadStore,
    job: ImportJob,
    extractor: TransactionSourceExtractor | None = None,
) -> ImportReview:
    """Reparse a mapped source into editable review rows without writing data."""
    if job.status != "reviewing":
        raise ImportStateError("not_ready_for_review", "Map the CSV before reviewing it.")
    document = load_source_document(store, job, extractor)
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
            decision = (
                categorize_candidate(session, job.workspace_id, normalized)
                if not duplicate
                else None
            )
            category = session.get(Category, decision.category_id) if decision else None
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
                    normalized_merchant=decision.normalized_merchant if decision else None,
                    category_id=decision.category_id if decision else None,
                    category_name=category.name if category else None,
                    is_subscription=decision.is_subscription if decision else None,
                    categorization_source=decision.source.value if decision else None,
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


def _accessible_category(session: Session, workspace_id: int, category_id: int) -> Category | None:
    return session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )


def _reviewed_fields(
    session: Session,
    workspace_id: int,
    candidate: NormalizedTransaction,
    review_row: ReviewRow,
    edit: RowEdit,
) -> tuple[str, int, bool, str]:
    if (
        review_row.category_id is None
        or review_row.normalized_merchant is None
        or review_row.is_subscription is None
        or review_row.categorization_source is None
    ):
        decision = categorize_candidate(session, workspace_id, candidate)
        fallback_merchant = decision.normalized_merchant
        fallback_category_id = decision.category_id
        fallback_subscription = decision.is_subscription
        fallback_source = decision.source.value
    else:
        fallback_merchant = review_row.normalized_merchant
        fallback_category_id = review_row.category_id
        fallback_subscription = review_row.is_subscription
        fallback_source = review_row.categorization_source

    merchant = (
        edit.normalized_merchant if edit.normalized_merchant is not None else fallback_merchant
    )
    merchant = " ".join(merchant.split())
    if not merchant or len(merchant) > 255:
        raise ValueError("merchant")
    category_id = edit.category_id if edit.category_id is not None else fallback_category_id
    if _accessible_category(session, workspace_id, category_id) is None:
        raise ValueError("category")
    subscription = (
        edit.is_subscription if edit.is_subscription is not None else fallback_subscription
    )
    if type(subscription) is not bool:
        raise ValueError("subscription")
    source = edit.categorization_source or fallback_source
    if source not in {item.value for item in CategorizationSource}:
        raise ValueError("source")

    has_original = any(
        value is not None
        for value in (
            edit.original_normalized_merchant,
            edit.original_category_id,
            edit.original_is_subscription,
            edit.original_categorization_source,
        )
    )
    baseline = (
        (
            edit.original_normalized_merchant,
            edit.original_category_id,
            edit.original_is_subscription,
            edit.original_categorization_source,
        )
        if has_original
        else (
            review_row.normalized_merchant,
            review_row.category_id,
            review_row.is_subscription,
            review_row.categorization_source,
        )
    )
    changed = (
        (merchant, category_id, subscription, source) != baseline
        or edit.date_value != review_row.date_value
        or edit.description_value != review_row.description_value
        or edit.amount_value != review_row.amount_value
    )
    return (
        merchant,
        category_id,
        subscription,
        CategorizationSource.MANUAL.value if changed else source,
    )


def commit_import(
    session: Session,
    store: LocalUploadStore,
    job: ImportJob,
    edits: tuple[RowEdit, ...],
    extractor: TransactionSourceExtractor | None = None,
) -> CommitResult:
    """Atomically persist reviewed non-duplicate edits, then honor retention."""
    if job.status in COMMITTED_STATUSES:
        return CommitResult(
            job=job,
            inserted_count=0,
            duplicate_count=0,
            excluded_count=0,
            cleanup_failed=job.status == "committed_cleanup_failed",
        )
    if job.status != "reviewing":
        raise ImportStateError("not_ready_to_commit", "Review the CSV before committing it.")

    review = build_review(session, store, job, extractor)
    expected_rows = tuple(row.row_number for row in review.rows)
    submitted_rows = tuple(edit.row_number for edit in edits)
    if submitted_rows != expected_rows or len(set(submitted_rows)) != len(submitted_rows):
        raise ImportStateError(
            "review_rows_changed", "The reviewed rows changed; reload the review page."
        )

    normalized: list[NormalizedTransaction] = []
    reviewed_fields: dict[int, tuple[str, int, bool, str]] = {}
    review_by_row = {row.row_number: row for row in review.rows}
    row_errors: dict[int, dict[str, str]] = {}
    for edit in edits:
        if not edit.include:
            continue
        try:
            candidate = normalize_review_edit(
                edit.row_number,
                edit.date_value,
                edit.description_value,
                edit.amount_value,
                "iso",
            )
            normalized.append(candidate)
            reviewed_fields[edit.row_number] = _reviewed_fields(
                session,
                job.workspace_id,
                candidate,
                review_by_row[edit.row_number],
                edit,
            )
        except RowValidationError as exc:
            row_errors[edit.row_number] = exc.field_errors
        except ValueError as exc:
            field = str(exc)
            row_errors[edit.row_number] = {field: "Choose a valid categorization value."}
    if row_errors:
        raise ReviewValidationError(
            "invalid_review_rows", "Correct the highlighted rows before committing.", row_errors
        )

    fingerprinted = fingerprint_transactions(tuple(normalized))
    existing = find_existing_fingerprints(
        session, job.workspace_id, {item.fingerprint for item in fingerprinted}
    )
    new_items = tuple(item for item in fingerprinted if item.fingerprint not in existing)
    known_duplicate_rows = {row.row_number for row in review.rows if row.duplicate}
    duplicate_count = len(existing) + sum(
        not edit.include and edit.row_number in known_duplicate_rows for edit in edits
    )
    excluded_count = sum(
        not edit.include and edit.row_number not in known_duplicate_rows for edit in edits
    )
    if not new_items:
        raise ReviewValidationError(
            "no_rows_selected", "Select at least one new valid transaction."
        )

    for item in new_items:
        transaction = item.transaction
        merchant, category_id, is_subscription, source = reviewed_fields[transaction.row_number]
        session.add(
            Transaction(
                workspace_id=job.workspace_id,
                date=datetime.combine(transaction.transaction_date, time.min, tzinfo=UTC),
                description=transaction.description,
                normalized_merchant=merchant,
                amount_cents=transaction.amount_cents,
                category_id=category_id,
                categorization_source=source,
                is_subscription=is_subscription,
                duplicate_fingerprint=item.fingerprint,
                import_job_id=job.id,
            )
        )
    job.status = "committed"
    job.validation_errors = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ImportStateError(
            "duplicate_commit_conflict",
            "Another request committed matching transactions; review again.",
        ) from exc

    cleanup_failed = False
    uploaded_file = job.uploaded_file
    if (
        uploaded_file is not None
        and uploaded_file.retention_choice == "delete_after_import"
        and not uploaded_file.deleted
    ):
        try:
            store.delete(uploaded_file.storage_path)
        except (OSError, UploadStorageError):
            cleanup_failed = True
            job.status = "committed_cleanup_failed"
            job.validation_errors = {"cleanup": "delete_failed"}
        else:
            uploaded_file.deleted = True
        session.commit()

    return CommitResult(
        job=job,
        inserted_count=len(new_items),
        duplicate_count=duplicate_count,
        excluded_count=excluded_count,
        cleanup_failed=cleanup_failed,
    )


def retry_cleanup(session: Session, store: LocalUploadStore, job: ImportJob) -> ImportJob:
    """Retry only a previously recorded post-commit or cancellation cleanup."""
    final_statuses = {
        "committed_cleanup_failed": "committed",
        "canceled_cleanup_failed": "canceled",
    }
    final_status = final_statuses.get(job.status)
    if final_status is None:
        raise ImportStateError("cleanup_not_required", "This import has no pending cleanup.")
    uploaded_file = job.uploaded_file
    if uploaded_file is not None and not uploaded_file.deleted:
        try:
            store.delete(uploaded_file.storage_path)
        except (OSError, UploadStorageError):
            return job
        uploaded_file.deleted = True
    job.status = final_status
    job.validation_errors = None
    session.commit()
    return job
