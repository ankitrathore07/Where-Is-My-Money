from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountBalanceSnapshot,
    AccountStatementImport,
    UploadedFile,
    Workspace,
)
from app.payslips.extraction import DocumentExtractionError, ExtractedText
from app.statement_imports.parsing import parse_wimm_csv, validate_statement_review
from app.statement_imports.processors import process_statement_text
from app.statement_imports.storage import StatementStorageError, StatementUploadStore
from app.statement_imports.types import StatementFormatError, compatible_account_types

RETENTION_CHOICES = {"retain", "delete_after_import"}
ALLOWED_CONTENT_TYPES = {
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
}


class CandidateExtractor(Protocol):
    def extract(self, data: bytes, suffix: str) -> ExtractedText: ...


class StatementImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_workspace_statement_import(
    session: Session, workspace_id: int, statement_import_id: int
) -> AccountStatementImport | None:
    return session.scalar(
        select(AccountStatementImport).where(
            AccountStatementImport.id == statement_import_id,
            AccountStatementImport.workspace_id == workspace_id,
        )
    )


def _validate_ingestion(
    category: str, filename: str, media_type: str, retention_choice: str
) -> str:
    try:
        compatible_account_types(category)
    except StatementFormatError as exc:
        raise StatementImportError(exc.code, exc.message) from exc
    if retention_choice not in RETENTION_CHOICES:
        raise StatementImportError(
            "invalid_retention", "Choose a valid private-file retention choice."
        )
    suffix = Path(filename).suffix.casefold()
    content_types = ALLOWED_CONTENT_TYPES.get(suffix)
    if content_types is None:
        raise StatementImportError(
            "unsupported_file_type", "Choose a CSV, PDF, PNG, or JPEG statement."
        )
    if media_type.casefold() not in content_types:
        raise StatementImportError(
            "content_type_mismatch", "The selected file type does not match its contents."
        )
    return suffix


def ingest_one_statement(
    session: Session,
    store: StatementUploadStore,
    extractor: CandidateExtractor,
    workspace: Workspace,
    declared_category: str,
    filename: str,
    media_type: str,
    stream: BinaryIO,
    retention_choice: str,
) -> AccountStatementImport:
    """Store and extract exactly one statement without creating a snapshot."""
    suffix = _validate_ingestion(declared_category, filename, media_type, retention_choice)
    stored = None
    try:
        stored = store.save(workspace.id, suffix, stream)
        existing = session.scalar(
            select(AccountStatementImport).where(
                AccountStatementImport.workspace_id == workspace.id,
                AccountStatementImport.statement_category == declared_category,
                AccountStatementImport.source_checksum == stored.checksum,
            )
        )
        if existing is not None:
            store.delete(stored.storage_key)
            return existing

        data = store.read(stored.storage_key)
        if suffix == ".csv":
            candidate = parse_wimm_csv(data)
        else:
            extracted = extractor.extract(data, suffix)
            candidate = process_statement_text(declared_category, extracted.text, extracted.method)
        uploaded = UploadedFile(
            workspace_id=workspace.id,
            file_type="account_statement",
            storage_path=stored.storage_key,
            checksum=stored.checksum,
            size_bytes=stored.size_bytes,
            retention_choice=retention_choice,
        )
        pending = AccountStatementImport(
            workspace_id=workspace.id,
            uploaded_file=uploaded,
            statement_category=declared_category,
            source_checksum=stored.checksum,
            candidate_fields=candidate.to_json(),
            review_status="pending",
        )
        session.add(pending)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            winner = session.scalar(
                select(AccountStatementImport).where(
                    AccountStatementImport.workspace_id == workspace.id,
                    AccountStatementImport.statement_category == declared_category,
                    AccountStatementImport.source_checksum == stored.checksum,
                )
            )
            if winner is None:
                raise
            store.delete(stored.storage_key)
            return winner
        return pending
    except DocumentExtractionError as exc:
        session.rollback()
        if stored is not None:
            try:
                store.delete(stored.storage_key)
            except (OSError, StatementStorageError) as cleanup_exc:
                raise StatementImportError(
                    "cleanup_failed", "The invalid private statement could not be removed."
                ) from cleanup_exc
        raise StatementImportError(exc.code, exc.message) from exc
    except Exception:
        session.rollback()
        if stored is not None:
            try:
                store.delete(stored.storage_key)
            except (OSError, StatementStorageError) as exc:
                raise StatementImportError(
                    "cleanup_failed", "The invalid private statement could not be removed."
                ) from exc
        raise


def list_compatible_accounts(
    session: Session, workspace_id: int, category: str
) -> tuple[Account, ...]:
    account_types = compatible_account_types(category)
    return tuple(
        session.scalars(
            select(Account)
            .where(
                Account.workspace_id == workspace_id,
                Account.account_type.in_(account_types),
            )
            .order_by(func.lower(Account.name), Account.id)
        )
    )


@dataclass(frozen=True)
class StatementConfirmationResult:
    snapshot: AccountBalanceSnapshot
    cleanup_failed: bool
    already_confirmed: bool


def _existing_statement_snapshot(
    session: Session, pending: AccountStatementImport
) -> AccountBalanceSnapshot | None:
    return session.scalar(
        select(AccountBalanceSnapshot).where(
            AccountBalanceSnapshot.statement_import_id == pending.id,
            AccountBalanceSnapshot.workspace_id == pending.workspace_id,
        )
    )


def confirm_statement_import(
    session: Session,
    store: StatementUploadStore,
    pending: AccountStatementImport,
    form: Mapping[str, str],
    *,
    today: date,
) -> StatementConfirmationResult:
    existing = _existing_statement_snapshot(session, pending)
    if existing is not None:
        return StatementConfirmationResult(
            existing,
            pending.review_status == "confirmed_cleanup_failed",
            True,
        )
    if pending.review_status != "pending":
        raise StatementImportError("not_pending", "This statement is not waiting for confirmation.")
    values = validate_statement_review(form, today=today)
    account = session.scalar(
        select(Account).where(
            Account.id == values.account_id,
            Account.workspace_id == pending.workspace_id,
            Account.account_type.in_(compatible_account_types(pending.statement_category)),
        )
    )
    if account is None:
        raise StatementImportError("account_not_found", "Account not found.")

    snapshot = AccountBalanceSnapshot(
        workspace_id=pending.workspace_id,
        account_id=account.id,
        balance_cents=values.balance_cents,
        as_of_date=values.as_of_date,
        source="statement_import",
        uploaded_file_id=pending.uploaded_file_id,
        statement_import_id=pending.id,
    )
    pending.account_id = account.id
    pending.confirmed_fields = values.to_json()
    pending.review_status = "confirmed"
    session.add(snapshot)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _existing_statement_snapshot(session, pending)
        if existing is None:
            raise
        return StatementConfirmationResult(existing, False, True)

    cleanup_failed = False
    uploaded = pending.uploaded_file
    if uploaded.retention_choice == "delete_after_import" and not uploaded.deleted:
        try:
            store.delete(uploaded.storage_path)
        except (OSError, StatementStorageError):
            cleanup_failed = True
            pending.review_status = "confirmed_cleanup_failed"
        else:
            uploaded.deleted = True
        session.commit()
    return StatementConfirmationResult(snapshot, cleanup_failed, False)


def retry_statement_source_cleanup(
    session: Session,
    store: StatementUploadStore,
    pending: AccountStatementImport,
) -> None:
    uploaded = pending.uploaded_file
    if uploaded.deleted:
        if pending.review_status == "confirmed_cleanup_failed":
            pending.review_status = "confirmed"
            session.commit()
        return
    if pending.review_status != "confirmed_cleanup_failed":
        raise StatementImportError(
            "cleanup_not_available", "This statement does not need source cleanup."
        )
    try:
        store.delete(uploaded.storage_path)
    except (OSError, StatementStorageError) as exc:
        raise StatementImportError(
            "cleanup_failed", "The private statement source still could not be deleted."
        ) from exc
    uploaded.deleted = True
    pending.review_status = "confirmed"
    session.commit()
