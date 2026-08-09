from dataclasses import dataclass
from typing import BinaryIO, Literal

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import ImportJob, UploadedFile, Workspace
from app.imports.parser import parse_csv_bytes
from app.imports.storage import LocalUploadStore, UploadStorageError

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
