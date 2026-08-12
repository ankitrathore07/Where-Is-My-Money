from pathlib import Path
from typing import BinaryIO, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AccountStatementImport, UploadedFile, Workspace
from app.payslips.extraction import ExtractedText
from app.statement_imports.parsing import parse_wimm_csv
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
        session.commit()
        return pending
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
