from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ImportJob, UploadedFile, Workspace
from app.imports.parser import CsvValidationError
from app.imports.service import (
    ImportStateError,
    cancel_import,
    create_csv_import,
    get_workspace_import,
)
from app.imports.storage import LocalUploadStore, UploadStorageError

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
