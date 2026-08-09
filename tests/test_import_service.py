import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.imports.service as import_service
from app.db.models import Category, ImportJob, Transaction, UploadedFile, Workspace
from app.imports.parser import CsvValidationError
from app.imports.service import (
    ImportStateError,
    ReviewValidationError,
    build_review,
    cancel_import,
    commit_import,
    create_csv_import,
    get_workspace_import,
    retry_cleanup,
    save_mapping,
)
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.imports.types import RowEdit

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example,-1.00\n"


class FailingDeleteStore(LocalUploadStore):
    def delete(self, storage_key: str) -> None:
        raise UploadStorageError("delete_failed", "Synthetic delete failure")


def test_create_job_links_private_file_and_checksum(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    result = create_csv_import(
        session,
        LocalUploadStore(tmp_path),
        workspace,
        BytesIO(CSV_BYTES),
        "delete_after_import",
    )

    assert result.kind == "created"
    assert result.job.status == "awaiting_mapping"
    assert result.job.workspace_id == workspace.id
    assert result.job.uploaded_file is not None
    assert result.job.uploaded_file.retention_choice == "delete_after_import"
    assert result.job.source_checksum == result.job.uploaded_file.checksum


def test_committed_exact_reupload_creates_no_second_job(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    first = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")
    first.job.status = "committed"
    session.commit()

    second = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")

    assert second.kind == "already_committed"
    assert second.job.id == first.job.id
    assert session.scalar(select(func.count()).select_from(ImportJob)) == 1
    assert len(list(tmp_path.rglob("*.csv"))) == 1


def test_active_exact_reupload_resumes_existing_job(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    first = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")

    second = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")

    assert second.kind == "resume"
    assert second.job.id == first.job.id
    assert session.scalar(select(func.count()).select_from(UploadedFile)) == 1


def test_import_lookup_hides_other_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    job = ImportJob(workspace_id=other_workspace.id, status="reviewing")
    session.add(job)
    session.commit()

    assert get_workspace_import(session, workspace.id, job.id) is None
    assert get_workspace_import(session, other_workspace.id, job.id) is job


def test_invalid_csv_deletes_source_and_creates_no_records(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    with pytest.raises(CsvValidationError):
        create_csv_import(
            session,
            LocalUploadStore(tmp_path),
            workspace,
            BytesIO(b"not|supported"),
            "delete_after_import",
        )

    assert session.scalar(select(func.count()).select_from(ImportJob)) == 0
    assert session.scalar(select(func.count()).select_from(UploadedFile)) == 0
    assert list(tmp_path.rglob("*.csv")) == []


def test_invalid_retention_is_rejected_before_storage(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    with pytest.raises(ImportStateError) as error:
        create_csv_import(
            session,
            LocalUploadStore(tmp_path),
            workspace,
            BytesIO(CSV_BYTES),
            "forever",
        )

    assert error.value.code == "invalid_retention"
    assert list(tmp_path.rglob("*.csv")) == []


def test_cancel_deletes_even_a_retained_uncommitted_source(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")

    cancel_import(session, store, created.job)

    assert created.job.status == "canceled"
    assert created.job.uploaded_file is not None
    assert created.job.uploaded_file.deleted is True
    assert list(tmp_path.rglob("*.csv")) == []


def test_cancel_records_cleanup_failure_without_losing_job_state(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    normal_store = LocalUploadStore(tmp_path)
    created = create_csv_import(
        session, normal_store, workspace, BytesIO(CSV_BYTES), "delete_after_import"
    )

    cancel_import(session, FailingDeleteStore(tmp_path), created.job)

    assert created.job.status == "canceled_cleanup_failed"
    assert created.job.validation_errors == {"cleanup": "delete_failed"}
    assert created.job.uploaded_file is not None
    assert created.job.uploaded_file.deleted is False


def test_committed_import_cannot_be_canceled(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")
    created.job.status = "committed"
    session.commit()

    with pytest.raises(ImportStateError) as error:
        cancel_import(session, store, created.job)

    assert error.value.code == "cannot_cancel"


def test_mapping_is_saved_and_can_change_during_review(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")
    form = {
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "single",
        "amount_column": "Amount",
        "date_format": "mdy",
        "amount_sign": "as_is",
    }

    save_mapping(session, store, created.job, form)

    assert created.job.status == "reviewing"
    assert created.job.column_mapping == {
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "single",
        "amount_column": "Amount",
        "debit_column": None,
        "credit_column": None,
        "date_format": "mdy",
        "amount_sign": "as_is",
    }

    save_mapping(session, store, created.job, form | {"amount_sign": "invert"})
    assert created.job.column_mapping["amount_sign"] == "invert"


def test_committed_mapping_is_not_editable(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")
    created.job.status = "committed"
    session.commit()

    with pytest.raises(ImportStateError) as error:
        save_mapping(session, store, created.job, {})

    assert error.value.code == "mapping_not_editable"


def test_review_reports_valid_invalid_and_existing_duplicate_rows(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    source = (
        b"Date,Description,Amount\n"
        b"08/01/2026,First,-1.00\n"
        b"08/02/2026,Second,-2.00\n"
        b"08/03/2026,Bad,nope\n"
        b"08/04/2026,Existing,-4.00\n"
    )
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(source), "retain")
    save_mapping(
        session,
        store,
        created.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    payload = "v1\n2026-08-04\n-400\nEXISTING\n1"
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    session.add(
        Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 8, 4, tzinfo=UTC),
            description="Existing",
            normalized_merchant="EXISTING",
            amount_cents=-400,
            duplicate_fingerprint=fingerprint,
        )
    )
    session.commit()

    review = build_review(session, store, created.job)

    assert review.total_rows == 4
    assert review.valid_rows == 3
    assert review.invalid_rows == 1
    assert review.duplicate_rows == 1
    assert review.rows[0].date_value == "2026-08-01"
    assert review.rows[0].amount_value == "-1.00"
    assert review.rows[0].included is True
    assert review.rows[2].field_errors == {"amount": "Enter a valid non-zero amount."}
    assert review.rows[3].duplicate is True
    assert review.rows[3].included is False


def test_review_requires_the_private_source(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    created = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "retain")
    save_mapping(
        session,
        store,
        created.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    assert created.job.uploaded_file is not None
    store.delete(created.job.uploaded_file.storage_path)

    with pytest.raises(ImportStateError) as error:
        build_review(session, store, created.job)

    assert error.value.code == "source_missing"


def mapped_import(
    session: Session,
    workspace: Workspace,
    store: LocalUploadStore,
    source: bytes = CSV_BYTES,
    retention: str = "delete_after_import",
) -> ImportJob:
    created = create_csv_import(session, store, workspace, BytesIO(source), retention)
    save_mapping(
        session,
        store,
        created.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    return created.job


def add_uncategorized(session: Session) -> Category:
    category = Category(workspace_id=None, name="Uncategorized", kind="expense")
    session.add(category)
    session.commit()
    return category


def test_commit_uses_reviewed_edits_and_exclusions_atomically(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(
        session,
        workspace,
        store,
        b"Date,Description,Amount\n08/01/2026,First,-12.34\n08/02/2026,Second,-2.00\n",
    )
    add_uncategorized(session)
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0

    result = commit_import(
        session,
        store,
        job,
        (
            RowEdit(2, True, "2026-08-01", "Corrected Market", "-12.34"),
            RowEdit(3, False, "2026-08-02", "Second", "-2.00"),
        ),
    )

    assert result.inserted_count == 1
    assert result.excluded_count == 1
    assert result.duplicate_count == 0
    assert result.job.status == "committed"
    transactions = list(session.scalars(select(Transaction)))
    assert len(transactions) == 1
    assert transactions[0].description == "Corrected Market"
    assert transactions[0].amount_cents == -1234
    assert transactions[0].date.date().isoformat() == "2026-08-01"
    assert transactions[0].category is not None
    assert transactions[0].category.name == "Uncategorized"
    assert transactions[0].categorization_source == "uncategorized"
    assert transactions[0].duplicate_fingerprint is not None
    assert job.uploaded_file is not None
    assert job.uploaded_file.deleted is True
    assert list(tmp_path.rglob("*.csv")) == []


def test_commit_rejects_changed_row_set(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, store)
    add_uncategorized(session)

    with pytest.raises(ImportStateError) as error:
        commit_import(session, store, job, (RowEdit(999, True, "2026-08-01", "X", "-1"),))

    assert error.value.code == "review_rows_changed"
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_commit_returns_row_errors_without_writing(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, store)
    add_uncategorized(session)

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (RowEdit(2, True, "2026-08-01", "", "0"),),
        )

    assert error.value.row_errors[2] == {
        "description": "Enter a description.",
        "amount": "Enter a valid non-zero amount.",
    }
    assert job.status == "reviewing"
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_second_commit_is_idempotent(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, store, retention="retain")
    add_uncategorized(session)
    edits = (RowEdit(2, True, "2026-08-01", "Example", "-1.00"),)

    first = commit_import(session, store, job, edits)
    second = commit_import(session, store, job, edits)

    assert first.inserted_count == 1
    assert second.inserted_count == 0
    assert session.scalar(select(func.count()).select_from(Transaction)) == 1
    assert job.uploaded_file is not None
    assert job.uploaded_file.deleted is False
    assert len(list(tmp_path.rglob("*.csv"))) == 1


def test_commit_requires_at_least_one_new_selected_row(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, store)
    add_uncategorized(session)

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (RowEdit(2, False, "2026-08-01", "Example", "-1.00"),),
        )

    assert error.value.code == "no_rows_selected"
    assert job.status == "reviewing"


def test_cleanup_failure_preserves_commit_and_can_be_retried(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    normal_store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, normal_store)
    add_uncategorized(session)

    result = commit_import(
        session,
        FailingDeleteStore(tmp_path),
        job,
        (RowEdit(2, True, "2026-08-01", "Example", "-1.00"),),
    )

    assert result.inserted_count == 1
    assert result.cleanup_failed is True
    assert job.status == "committed_cleanup_failed"
    assert job.validation_errors == {"cleanup": "delete_failed"}
    assert session.scalar(select(func.count()).select_from(Transaction)) == 1

    retry_cleanup(session, normal_store, job)

    assert job.status == "committed"
    assert job.validation_errors is None
    assert job.uploaded_file is not None
    assert job.uploaded_file.deleted is True


def test_concurrent_duplicate_rolls_back_the_entire_commit(
    session: Session,
    workspace: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalUploadStore(tmp_path)
    job = mapped_import(session, workspace, store, retention="retain")
    add_uncategorized(session)
    payload = "v1\n2026-08-01\n-100\nEXAMPLE\n1"
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    session.add(
        Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 8, 1, tzinfo=UTC),
            description="Example",
            normalized_merchant="EXAMPLE",
            amount_cents=-100,
            duplicate_fingerprint=fingerprint,
        )
    )
    session.commit()
    monkeypatch.setattr(import_service, "find_existing_fingerprints", lambda *args: set())

    with pytest.raises(ImportStateError) as error:
        commit_import(
            session,
            store,
            job,
            (RowEdit(2, True, "2026-08-01", "Example", "-1.00"),),
        )

    assert error.value.code == "duplicate_commit_conflict"
    assert session.scalar(select(func.count()).select_from(Transaction)) == 1
    assert session.get(ImportJob, job.id).status == "reviewing"
