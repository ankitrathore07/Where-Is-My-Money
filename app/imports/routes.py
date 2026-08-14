"""Authenticated, workspace-scoped transaction-statement import pages."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.categories.service import list_accessible_categories
from app.core.middleware import require_csrf
from app.db.models import ImportJob, User, Workspace
from app.db.session import get_db
from app.imports.document_parser import TransactionStatementFormatError
from app.imports.mapping import MappingValidationError
from app.imports.parser import CsvValidationError
from app.imports.review_tokens import (
    ReviewTokenError,
    create_review_token,
    load_review_token,
)
from app.imports.service import (
    ImportStateError,
    ReviewValidationError,
    build_review,
    cancel_import,
    commit_import,
    create_transaction_import,
    get_workspace_import,
    load_source_document,
    retry_cleanup,
    save_mapping,
)
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.imports.types import RowEdit
from app.payslips.extraction import DocumentExtractionError
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["imports"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
ALLOWED_CONTENT_TYPES = {
    ".csv": {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/octet-stream",
    },
    ".pdf": {"application/pdf"},
}
CATEGORIZATION_SOURCE_LABELS = {
    "workspace_rule": "Workspace rule",
    "provider_rule": "Provider rule",
    "builtin_rule": "Built-in rule",
    "ai_suggestion": "AI suggestion",
    "uncategorized": "Uncategorized",
    "manual": "Manual",
}


def _context(
    request: Request, user: User, workspace: Workspace, **values: object
) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "csrf_token": request.state.csrf_token,
        "workspace": workspace,
        **values,
    }


def _store(request: Request) -> LocalUploadStore:
    configured = request.app.state.settings
    return getattr(
        request.app.state,
        "upload_store",
        LocalUploadStore(
            configured.upload_directory,
            configured.max_csv_upload_bytes,
            configured.max_statement_upload_bytes,
        ),
    )


def _extractor(request: Request):
    return request.app.state.statement_extractor


def _job_or_404(session: Session, workspace: Workspace, import_id: int) -> ImportJob:
    job = get_workspace_import(session, workspace.id, import_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job


def _mapping_values(job: ImportJob) -> dict[str, object]:
    return job.column_mapping if isinstance(job.column_mapping, dict) else {}


def _optional_form_int(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None and str(value) else None
    except ValueError:
        return None


def _mapping_page(
    request: Request,
    user: User,
    workspace: Workspace,
    job: ImportJob,
    store: LocalUploadStore,
    *,
    errors: dict[str, str] | None = None,
    values: dict[str, object] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    document = load_source_document(store, job, _extractor(request))
    return templates.TemplateResponse(
        request=request,
        name="imports/mapping.html",
        context=_context(
            request,
            user,
            workspace,
            job=job,
            headers=document.headers,
            preview_rows=document.rows[:10],
            errors=errors or {},
            values=values or _mapping_values(job),
        ),
        status_code=status_code,
    )


def _review_page(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    job: ImportJob,
    *,
    error: str | None = None,
    row_errors: dict[int, dict[str, str]] | None = None,
    submitted_edits: tuple[RowEdit, ...] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    review = build_review(
        session,
        _store(request),
        job,
        _extractor(request),
        categorization_graph=request.app.state.categorization_graph,
    )
    secret_key = request.app.state.settings.secret_key or ""
    return templates.TemplateResponse(
        request=request,
        name="imports/review.html",
        context=_context(
            request,
            user,
            workspace,
            job=job,
            review=review,
            error=error,
            row_errors=row_errors or {},
            submitted=bool(submitted_edits is not None),
            submitted_by_row={edit.row_number: edit for edit in submitted_edits or ()},
            category_choices=list_accessible_categories(session, workspace.id),
            categorization_source_labels=CATEGORIZATION_SOURCE_LABELS,
            review_tokens={
                row.row_number: create_review_token(secret_key, job.id, row) for row in review.rows
            },
        ),
        status_code=status_code,
    )


@router.get("/imports/new", response_class=HTMLResponse)
async def new_import(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Show the private transaction-statement upload form."""
    return templates.TemplateResponse(
        request=request,
        name="imports/upload.html",
        context=_context(request, user, workspace),
    )


@router.post("/imports", dependencies=[Depends(require_csrf)])
async def upload_import(
    request: Request,
    statement: Annotated[UploadFile, File()],
    retention_choice: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Validate and privately store one CSV/PDF, then start or resume review."""
    filename = statement.filename or ""
    suffix = Path(filename).suffix.casefold()
    if suffix not in ALLOWED_CONTENT_TYPES or (
        statement.content_type
        and statement.content_type.casefold() not in ALLOWED_CONTENT_TYPES[suffix]
    ):
        return templates.TemplateResponse(
            request=request,
            name="imports/upload.html",
            context=_context(
                request, user, workspace, error="Choose a CSV or PDF transaction statement."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = create_transaction_import(
            session,
            _store(request),
            _extractor(request),
            workspace,
            filename,
            statement.content_type or "",
            statement.file,
            retention_choice,
        )
    except (
        CsvValidationError,
        UploadStorageError,
        ImportStateError,
        DocumentExtractionError,
        TransactionStatementFormatError,
    ) as exc:
        return templates.TemplateResponse(
            request=request,
            name="imports/upload.html",
            context=_context(request, user, workspace, error=str(exc)),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if result.kind == "already_committed":
        destination = f"/workspaces/{workspace.id}/transactions?already_imported=1"
    elif result.job.status == "reviewing":
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/review"
    else:
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/mapping"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/imports/{import_id}/mapping", response_class=HTMLResponse)
async def map_import(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Preview the exact source and choose its semantic columns."""
    return _mapping_page(
        request,
        user,
        workspace,
        _job_or_404(session, workspace, import_id),
        _store(request),
    )


@router.post("/imports/{import_id}/mapping", dependencies=[Depends(require_csrf)])
async def update_mapping(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Persist a valid mapping and advance to review."""
    job = _job_or_404(session, workspace, import_id)
    form = await request.form()
    values = {key: str(value) for key, value in form.items() if key != "csrf_token"}
    try:
        save_mapping(session, _store(request), job, values, _extractor(request))
    except MappingValidationError as exc:
        return _mapping_page(
            request,
            user,
            workspace,
            job,
            _store(request),
            errors=exc.field_errors,
            values=values,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except ImportStateError as exc:
        return _mapping_page(
            request,
            user,
            workspace,
            job,
            _store(request),
            errors={"mapping": str(exc)},
            values=values,
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/imports/{job.id}/review",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/imports/{import_id}/review", response_class=HTMLResponse)
async def review_import(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Show normalized, editable rows without writing transactions."""
    job = _job_or_404(session, workspace, import_id)
    try:
        return _review_page(request, user, session, workspace, job)
    except ImportStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/imports/{import_id}/commit", dependencies=[Depends(require_csrf)])
async def commit_review(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Atomically commit the exact submitted review rows."""
    job = _job_or_404(session, workspace, import_id)
    form = await request.form()
    try:
        row_numbers = tuple(int(value) for value in form.getlist("row_numbers"))
    except (TypeError, ValueError):
        row_numbers = ()
    secret_key = request.app.state.settings.secret_key or ""
    edits_list: list[RowEdit] = []
    try:
        for row_number in row_numbers:
            baseline = load_review_token(
                secret_key,
                str(form.get(f"review_token_{row_number}", "")),
                job.id,
                row_number,
            )
            edits_list.append(
                RowEdit(
                    row_number=row_number,
                    include=form.get(f"include_{row_number}") is not None,
                    date_value=str(form.get(f"date_{row_number}", "")),
                    description_value=str(form.get(f"description_{row_number}", "")),
                    amount_value=str(form.get(f"amount_{row_number}", "")),
                    normalized_merchant=(
                        str(form.get(f"normalized_merchant_{row_number}"))
                        if form.get(f"normalized_merchant_{row_number}") is not None
                        else None
                    ),
                    category_id=_optional_form_int(form.get(f"category_{row_number}")),
                    is_subscription=(
                        form.get(f"is_subscription_{row_number}") is not None
                        if form.get(f"category_{row_number}") is not None
                        else None
                    ),
                    categorization_source=baseline.categorization_source,
                    original_normalized_merchant=baseline.normalized_merchant,
                    original_category_id=baseline.category_id,
                    original_is_subscription=baseline.is_subscription,
                    original_categorization_source=baseline.categorization_source,
                )
            )
    except ReviewTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    edits = tuple(edits_list)
    try:
        commit_import(session, _store(request), job, edits, _extractor(request))
    except ReviewValidationError as exc:
        return _review_page(
            request,
            user,
            session,
            workspace,
            job,
            error=exc.message,
            row_errors=exc.row_errors,
            submitted_edits=edits,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except ImportStateError as exc:
        return _review_page(
            request,
            user,
            session,
            workspace,
            job,
            error=exc.message,
            submitted_edits=edits,
            status_code=status.HTTP_409_CONFLICT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/imports/{job.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/imports/{import_id}", response_class=HTMLResponse)
async def import_result(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Show a truthful final import and source-retention status."""
    job = _job_or_404(session, workspace, import_id)
    return templates.TemplateResponse(
        request=request,
        name="imports/result.html",
        context=_context(request, user, workspace, job=job),
    )


@router.post("/imports/{import_id}/cancel", dependencies=[Depends(require_csrf)])
async def cancel_pending_import(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Cancel an uncommitted import and remove its private source."""
    job = _job_or_404(session, workspace, import_id)
    try:
        cancel_import(session, _store(request), job)
    except ImportStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    destination = (
        f"/workspaces/{workspace.id}/imports/{job.id}"
        if job.status == "canceled_cleanup_failed"
        else f"/workspaces/{workspace.id}"
    )
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/imports/{import_id}/cleanup", dependencies=[Depends(require_csrf)])
async def retry_import_cleanup(
    request: Request,
    import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Retry a recorded source-file cleanup failure."""
    job = _job_or_404(session, workspace, import_id)
    try:
        retry_cleanup(session, _store(request), job)
    except ImportStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    return RedirectResponse(
        f"/workspaces/{workspace.id}/imports/{job.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
