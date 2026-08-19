from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.categorization.ai_graph import CompiledCategorizationGraph, suggest_category
from app.categorization.ai_types import CategorySuggestion
from app.categorization.events import CategorizationEventReason, record_categorization_event
from app.categorization.sanitization import sanitize_transaction_description
from app.categorization.service import categorize_candidate
from app.categorization.types import CategorizationDecision, CategorizationSource
from app.db.models import Account, Category, ImportJob, Transaction, UploadedFile, Workspace
from app.imports.document_parser import parse_transaction_statement_text
from app.imports.duplicates import find_existing_fingerprints, fingerprint_transactions
from app.imports.mapping import mapping_from_json, validate_mapping
from app.imports.normalization import (
    RowValidationError,
    normalize_review_edit,
    normalize_source_row,
)
from app.imports.parser import parse_csv_bytes
from app.imports.providers.registry import parse_provider_pdf, resolve_provider_profile
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.imports.types import (
    ColumnMapping,
    CsvDocument,
    ImportReview,
    NormalizedTransaction,
    ReviewRow,
    RowEdit,
)
from app.rules.evaluation import CompiledWorkspaceRuleSet
from app.rules.loader import load_compiled_rule_set
from app.tags.service import (
    TagNotFoundError,
    accessible_tags_by_id,
    tag_ids_with_subscription,
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


@dataclass(frozen=True)
class ParsedSource:
    document: CsvDocument
    provider_key: str | None


@dataclass(frozen=True)
class _ImportCategorizationContext:
    provider_key: str | None
    account_id: int | None
    workspace_rules: CompiledWorkspaceRuleSet


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


def _matching_import(
    session: Session,
    workspace_id: int,
    checksum: str,
    account_id: int | None = None,
) -> ImportJob | None:
    account_clause = (
        ImportJob.account_id == account_id
        if account_id is not None
        else ImportJob.account_id.is_(None)
    )
    return session.scalar(
        select(ImportJob)
        .where(
            ImportJob.workspace_id == workspace_id,
            ImportJob.source_checksum == checksum,
            account_clause,
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
    *,
    account: Account | None = None,
) -> UploadResult:
    """Validate a private CSV source and create or resume its import job."""
    if retention_choice not in RETENTION_CHOICES:
        raise ImportStateError(
            "invalid_retention", "Choose whether to delete or retain the source file."
        )

    saved = store.save(workspace.id, upload)
    try:
        document = parse_csv_bytes(store.read(saved.storage_key))
        provider_mapping = None
        if account is not None:
            provider_mapping = resolve_provider_profile(
                account.institution_key,
                account.account_type,
                ".csv",
                document.headers,
            ).mapping
        existing = _matching_import(
            session,
            workspace.id,
            saved.checksum,
            account.id if account is not None else None,
        )
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
            account=account,
            status="reviewing" if provider_mapping is not None else "awaiting_mapping",
            column_mapping=(provider_mapping.to_json() if provider_mapping is not None else None),
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
    *,
    account: Account | None = None,
) -> UploadResult:
    """Create a reviewed CSV or locally extracted PDF transaction import."""
    suffix = Path(filename).suffix.casefold()
    if suffix == ".csv":
        return create_csv_import(
            session,
            store,
            workspace,
            upload,
            retention_choice,
            account=account,
        )
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
        _parse_pdf_source(account, extracted.text)
        existing = _matching_import(
            session,
            workspace.id,
            saved.checksum,
            account.id if account is not None else None,
        )
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
            account=account,
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


def _parse_pdf_source(account: Account | None, text: str) -> ParsedSource:
    provider = None
    if account is not None:
        provider = parse_provider_pdf(
            account.institution_key,
            account.account_type,
            text,
        )
    if provider is not None:
        return ParsedSource(provider.document, provider.profile_key)
    return ParsedSource(parse_transaction_statement_text(text), None)


def _load_source(
    store: LocalUploadStore,
    job: ImportJob,
    extractor: TransactionSourceExtractor | None = None,
) -> ParsedSource:
    uploaded_file = job.uploaded_file
    if uploaded_file is None or uploaded_file.deleted:
        raise ImportStateError("source_missing", "The private source file is missing.")
    try:
        data = store.read(uploaded_file.storage_path)
    except UploadStorageError as exc:
        raise ImportStateError("source_missing", "The private source file is missing.") from exc
    suffix = Path(uploaded_file.storage_path).suffix.casefold()
    if suffix == ".csv":
        document = parse_csv_bytes(data)
        provider_key = None
        if job.account is not None:
            provider_key = resolve_provider_profile(
                job.account.institution_key,
                job.account.account_type,
                suffix,
                document.headers,
            ).profile_key
        return ParsedSource(document, provider_key)
    if suffix == ".pdf" and extractor is not None:
        return _parse_pdf_source(job.account, extractor.extract(data, suffix).text)
    raise ImportStateError("source_unreadable", "The private transaction statement cannot be read.")


def load_source_document(
    store: LocalUploadStore,
    job: ImportJob,
    extractor: TransactionSourceExtractor | None = None,
) -> CsvDocument:
    """Load a stored statement while preserving the public document-only contract."""
    return _load_source(store, job, extractor).document


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


def _ai_categories(session: Session) -> tuple[Category, ...]:
    return tuple(
        session.scalars(
            select(Category)
            .where(
                Category.workspace_id.is_(None),
                Category.name != "Uncategorized",
            )
            .order_by(Category.name)
        )
    )


def _category_matches_direction(category: Category, amount_cents: int) -> bool:
    return (
        category.kind == "transfer"
        or (category.kind == "expense" and amount_cents < 0)
        or (category.kind == "income" and amount_cents > 0)
    )


def _apply_ai_suggestion(
    candidate: NormalizedTransaction,
    decision: CategorizationDecision,
    graph: CompiledCategorizationGraph | None,
    categories_by_name: Mapping[str, Category],
    suggestion_cache: dict[str, CategorySuggestion | None],
) -> CategorizationDecision:
    if graph is None or decision.source is not CategorizationSource.UNCATEGORIZED:
        return decision
    cache_key = sanitize_transaction_description(candidate.description)
    if not cache_key:
        return decision
    if cache_key not in suggestion_cache:
        suggestion_cache[cache_key] = suggest_category(
            graph,
            candidate.description,
            tuple(categories_by_name),
        )
    suggestion = suggestion_cache[cache_key]
    category = categories_by_name.get(suggestion.category_name) if suggestion is not None else None
    if category is None or not _category_matches_direction(category, candidate.amount_cents):
        return decision
    return CategorizationDecision(
        normalized_merchant=decision.normalized_merchant,
        category_id=category.id,
        is_subscription=suggestion.is_subscription,
        source=CategorizationSource.AI_SUGGESTION,
        tag_ids=decision.tag_ids,
        billing_period_months=decision.billing_period_months,
    )


def build_review(
    session: Session,
    store: LocalUploadStore,
    job: ImportJob,
    extractor: TransactionSourceExtractor | None = None,
    *,
    categorization_graph: CompiledCategorizationGraph | None = None,
    _source: ParsedSource | None = None,
    _categorization_context: _ImportCategorizationContext | None = None,
) -> ImportReview:
    """Reparse a mapped source into editable review rows without writing data."""
    if job.status != "reviewing":
        raise ImportStateError(
            "not_ready_for_review", "Prepare the transaction statement before reviewing it."
        )
    source = _source or _load_source(store, job, extractor)
    document = source.document
    if not isinstance(job.column_mapping, dict):
        raise ImportStateError(
            "mapping_missing", "Prepare the transaction statement before reviewing it."
        )
    mapping = mapping_from_json(document.headers, job.column_mapping)
    categorization_context = _categorization_context or _ImportCategorizationContext(
        provider_key=source.provider_key,
        account_id=job.account_id,
        workspace_rules=load_compiled_rule_set(session, job.workspace_id),
    )
    ai_categories = _ai_categories(session) if categorization_graph is not None else ()
    ai_categories_by_name = {category.name: category for category in ai_categories}
    suggestion_cache: dict[str, CategorySuggestion | None] = {}

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
            decision = None
            if not duplicate:
                decision = categorize_candidate(
                    session,
                    job.workspace_id,
                    normalized,
                    provider_key=categorization_context.provider_key,
                    account_id=categorization_context.account_id,
                    workspace_rules=categorization_context.workspace_rules,
                )
                decision = _apply_ai_suggestion(
                    normalized,
                    decision,
                    categorization_graph,
                    ai_categories_by_name,
                    suggestion_cache,
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
                    tag_ids=(
                        tag_ids_with_subscription(
                            session,
                            decision.tag_ids,
                            decision.is_subscription,
                        )
                        if decision
                        else ()
                    ),
                    billing_period_months=(decision.billing_period_months if decision else None),
                    merchant_rule_id=decision.merchant_rule_id if decision else None,
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
    categorization_context: _ImportCategorizationContext,
) -> tuple[str, int, bool, str, tuple[int, ...], int | None, int | None]:
    if (
        review_row.category_id is None
        or review_row.normalized_merchant is None
        or review_row.is_subscription is None
        or review_row.categorization_source is None
    ):
        decision = categorize_candidate(
            session,
            workspace_id,
            candidate,
            provider_key=categorization_context.provider_key,
            account_id=categorization_context.account_id,
            workspace_rules=categorization_context.workspace_rules,
        )
        fallback_merchant = decision.normalized_merchant
        fallback_category_id = decision.category_id
        fallback_subscription = decision.is_subscription
        fallback_source = decision.source.value
        fallback_tag_ids = decision.tag_ids
        fallback_billing_period_months = decision.billing_period_months
        fallback_merchant_rule_id = decision.merchant_rule_id
    else:
        fallback_merchant = review_row.normalized_merchant
        fallback_category_id = review_row.category_id
        fallback_subscription = review_row.is_subscription
        fallback_source = review_row.categorization_source
        fallback_tag_ids = review_row.tag_ids
        fallback_billing_period_months = review_row.billing_period_months
        fallback_merchant_rule_id = review_row.merchant_rule_id

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
    selected_tag_ids = edit.tag_ids if edit.tag_ids is not None else fallback_tag_ids
    try:
        selected_tag_ids = tag_ids_with_subscription(
            session,
            selected_tag_ids,
            subscription,
        )
        tags = accessible_tags_by_id(session, workspace_id, selected_tag_ids)
    except TagNotFoundError as exc:
        raise ValueError("tags") from exc
    tag_ids = tuple(tag.id for tag in tags)
    billing_period_months = (
        edit.billing_period_months
        if edit.billing_period_submitted or edit.billing_period_months is not None
        else fallback_billing_period_months
    )
    if billing_period_months is not None and (
        type(billing_period_months) is not int
        or billing_period_months < 1
        or billing_period_months > 120
    ):
        raise ValueError("billing_period_months")

    has_original = any(
        value is not None
        for value in (
            edit.original_normalized_merchant,
            edit.original_category_id,
            edit.original_is_subscription,
            edit.original_categorization_source,
            edit.original_tag_ids,
            edit.original_billing_period_months,
        )
    )
    original_subscription = (
        edit.original_is_subscription
        if edit.original_is_subscription is not None
        else review_row.is_subscription
    )
    original_tag_ids = (
        edit.original_tag_ids if edit.original_tag_ids is not None else review_row.tag_ids
    )
    try:
        original_tag_ids = tag_ids_with_subscription(
            session,
            original_tag_ids,
            bool(original_subscription),
        )
        original_tags = accessible_tags_by_id(session, workspace_id, original_tag_ids)
    except TagNotFoundError as exc:
        raise ValueError("tags") from exc
    original_billing_period_months = (
        edit.original_billing_period_months
        if edit.original_tag_ids is not None
        else review_row.billing_period_months
    )
    baseline = (
        (
            edit.original_normalized_merchant,
            edit.original_category_id,
            edit.original_is_subscription,
            edit.original_categorization_source,
            tuple(sorted(tag.id for tag in original_tags)),
            original_billing_period_months,
        )
        if has_original
        else (
            review_row.normalized_merchant,
            review_row.category_id,
            review_row.is_subscription,
            review_row.categorization_source,
            tuple(sorted(review_row.tag_ids)),
            review_row.billing_period_months,
        )
    )
    changed = (
        (
            merchant,
            category_id,
            subscription,
            source,
            tuple(sorted(tag_ids)),
            billing_period_months,
        )
        != baseline
        or edit.date_value != review_row.date_value
        or edit.description_value != review_row.description_value
        or edit.amount_value != review_row.amount_value
    )
    baseline_merchant_rule_id = edit.merchant_rule_id if has_original else fallback_merchant_rule_id
    current_decision = (
        review_row.normalized_merchant,
        review_row.category_id,
        review_row.is_subscription,
        review_row.categorization_source,
        tuple(sorted(review_row.tag_ids)),
        review_row.billing_period_months,
    )
    merchant_rule_id = (
        baseline_merchant_rule_id
        if not changed
        and current_decision == baseline
        and source == CategorizationSource.WORKSPACE_RULE.value
        and baseline_merchant_rule_id is not None
        and baseline_merchant_rule_id == review_row.merchant_rule_id
        else None
    )
    return (
        merchant,
        category_id,
        subscription,
        CategorizationSource.MANUAL.value if changed else source,
        tag_ids,
        billing_period_months,
        merchant_rule_id,
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
        raise ImportStateError(
            "not_ready_to_commit", "Review the transaction statement before committing it."
        )

    source = _load_source(store, job, extractor)
    categorization_context = _ImportCategorizationContext(
        provider_key=source.provider_key,
        account_id=job.account_id,
        workspace_rules=load_compiled_rule_set(session, job.workspace_id),
    )
    review = build_review(
        session,
        store,
        job,
        extractor,
        _source=source,
        _categorization_context=categorization_context,
    )
    expected_rows = tuple(row.row_number for row in review.rows)
    submitted_rows = tuple(edit.row_number for edit in edits)
    if submitted_rows != expected_rows or len(set(submitted_rows)) != len(submitted_rows):
        raise ImportStateError(
            "review_rows_changed", "The reviewed rows changed; reload the review page."
        )

    normalized: list[NormalizedTransaction] = []
    reviewed_fields: dict[
        int, tuple[str, int, bool, str, tuple[int, ...], int | None, int | None]
    ] = {}
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
                categorization_context,
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

    persisted_transactions: list[Transaction] = []
    for item in new_items:
        transaction = item.transaction
        (
            merchant,
            category_id,
            is_subscription,
            source,
            tag_ids,
            billing_period_months,
            merchant_rule_id,
        ) = reviewed_fields[transaction.row_number]
        persisted = Transaction(
            workspace_id=job.workspace_id,
            date=datetime.combine(transaction.transaction_date, time.min, tzinfo=UTC),
            description=transaction.description,
            normalized_merchant=merchant,
            amount_cents=transaction.amount_cents,
            category_id=category_id,
            merchant_rule_id=merchant_rule_id,
            categorization_source=source,
            is_subscription=is_subscription,
            billing_period_months=billing_period_months,
            duplicate_fingerprint=item.fingerprint,
            import_job_id=job.id,
        )
        persisted.tags = list(accessible_tags_by_id(session, job.workspace_id, tag_ids))
        session.add(persisted)
        persisted_transactions.append(persisted)
    job.status = "committed"
    job.validation_errors = None
    try:
        session.flush()
        for persisted in persisted_transactions:
            record_categorization_event(
                session,
                workspace_id=job.workspace_id,
                transaction_id=persisted.id,
                previous_source=CategorizationSource.UNCATEGORIZED,
                new_source=persisted.categorization_source,
                previous_rule_id=None,
                new_rule_id=persisted.merchant_rule_id,
                reason=CategorizationEventReason.IMPORT_COMMIT,
            )
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
