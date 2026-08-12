from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AccountBalanceSnapshot, AccountStatementImport, UploadedFile, Workspace
from app.payslips.extraction import DocumentExtractionError, ExtractedText
from app.statement_imports.service import StatementImportError, ingest_one_statement
from app.statement_imports.storage import StatementUploadStore

CSV_BYTES = (
    b"account_name,institution,account_last_four,total_balance,as_of_date\n"
    b"Northstar Brokerage,Fictional Brokerage,4821,125430.18,2026-07-31\n"
)


class StaticExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        return ExtractedText(
            "Provider: Northstar Financial\nAccount ending in: 4821\n"
            "Statement date: 2026-07-31\nTotal account value: $125,430.18",
            "embedded_text",
        )


class FailingExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        raise DocumentExtractionError(
            "encrypted_pdf", "Encrypted PDF statements are not supported."
        )


def test_ingest_csv_creates_pending_review_without_snapshot(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    pending = ingest_one_statement(
        session,
        StatementUploadStore(tmp_path),
        StaticExtractor(),
        workspace,
        "brokerage",
        "statement.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    assert pending.review_status == "pending"
    assert pending.candidate_fields["balance_cents"] == 12_543_018
    assert pending.uploaded_file.file_type == "account_statement"
    assert pending.uploaded_file.retention_choice == "retain"
    assert session.query(AccountBalanceSnapshot).count() == 0


def test_ingest_document_uses_declared_category_processor(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    pending = ingest_one_statement(
        session,
        StatementUploadStore(tmp_path),
        StaticExtractor(),
        workspace,
        "brokerage",
        "statement.pdf",
        "application/pdf",
        BytesIO(b"%PDF-synthetic"),
        "delete_after_import",
    )
    assert pending.statement_category == "brokerage"
    assert pending.candidate_fields["extraction_method"] == "embedded_text"


def test_exact_reupload_resumes_existing_import_and_removes_new_source(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    store = StatementUploadStore(tmp_path)
    first = ingest_one_statement(
        session,
        store,
        StaticExtractor(),
        workspace,
        "brokerage",
        "statement.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    second = ingest_one_statement(
        session,
        store,
        StaticExtractor(),
        workspace,
        "brokerage",
        "statement.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    assert second.id == first.id
    assert session.query(AccountStatementImport).count() == 1
    assert session.query(UploadedFile).count() == 1
    assert len(list(tmp_path.rglob("*.csv"))) == 1


def test_same_source_can_retry_under_corrected_category(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    store = StatementUploadStore(tmp_path)
    first = ingest_one_statement(
        session,
        store,
        StaticExtractor(),
        workspace,
        "other",
        "s.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    second = ingest_one_statement(
        session,
        store,
        StaticExtractor(),
        workspace,
        "brokerage",
        "s.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    assert first.id != second.id


def test_document_extraction_error_is_safe_and_removes_source(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    with pytest.raises(StatementImportError) as error:
        ingest_one_statement(
            session,
            StatementUploadStore(tmp_path),
            FailingExtractor(),
            workspace,
            "brokerage",
            "statement.pdf",
            "application/pdf",
            BytesIO(b"%PDF-encrypted"),
            "retain",
        )
    assert error.value.code == "encrypted_pdf"
    assert list(tmp_path.rglob("*.pdf")) == []
    assert session.query(AccountStatementImport).count() == 0


def test_concurrent_duplicate_commit_returns_winning_import(
    tmp_path: Path,
    session: Session,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StatementUploadStore(tmp_path)
    checksum = "bc2ba672be070fe5372273280a827f54c7b1348b73075e4a38e7b578c7d07577"
    original_commit = session.commit
    intercepted = False

    def concurrent_commit() -> None:
        nonlocal intercepted
        if intercepted:
            original_commit()
            return
        intercepted = True
        session.rollback()
        winner_file = UploadedFile(
            workspace_id=workspace.id,
            file_type="account_statement",
            storage_path=f"{workspace.id}/{'f' * 32}.csv",
            checksum=checksum,
            size_bytes=len(CSV_BYTES),
        )
        winner = AccountStatementImport(
            workspace_id=workspace.id,
            uploaded_file=winner_file,
            statement_category="brokerage",
            source_checksum=checksum,
            candidate_fields={"balance_cents": 12_543_018},
            review_status="pending",
        )
        session.add(winner)
        original_commit()
        raise IntegrityError("insert", {}, Exception("unique conflict"))

    monkeypatch.setattr(session, "commit", concurrent_commit)
    result = ingest_one_statement(
        session,
        store,
        StaticExtractor(),
        workspace,
        "brokerage",
        "statement.csv",
        "text/csv",
        BytesIO(CSV_BYTES),
        "retain",
    )
    assert result.source_checksum == checksum
    assert session.query(AccountStatementImport).count() == 1


@pytest.mark.parametrize(
    ("category", "filename", "media_type", "retention", "code"),
    [
        ("checking", "s.csv", "text/csv", "retain", "unsupported_category"),
        ("brokerage", "s.exe", "text/csv", "retain", "unsupported_file_type"),
        ("brokerage", "s.csv", "application/pdf", "retain", "content_type_mismatch"),
        ("brokerage", "s.csv", "text/csv", "forever", "invalid_retention"),
    ],
)
def test_invalid_ingestion_is_rejected_before_database_write(
    tmp_path: Path,
    session: Session,
    workspace: Workspace,
    category: str,
    filename: str,
    media_type: str,
    retention: str,
    code: str,
) -> None:
    with pytest.raises(StatementImportError) as error:
        ingest_one_statement(
            session,
            StatementUploadStore(tmp_path),
            StaticExtractor(),
            workspace,
            category,
            filename,
            media_type,
            BytesIO(CSV_BYTES),
            retention,
        )
    assert error.value.code == code
    assert session.scalar(select(AccountStatementImport.id)) is None
