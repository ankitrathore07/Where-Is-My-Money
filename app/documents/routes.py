"""Authenticated, workspace-scoped document upload dispatch."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import User, Workspace
from app.db.session import get_db
from app.documents.catalog import (
    MAX_QUEUE_FILES,
    DocumentUploadValidationError,
    client_catalog,
    validate_processable_upload,
)
from app.documents.types import DocumentProcessResult
from app.imports.parser import CsvValidationError
from app.imports.service import ImportStateError, create_csv_import
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.payslips.extraction import DocumentExtractionError, DocumentExtractor
from app.payslips.service import PayslipImportError, create_payslip_import
from app.payslips.storage import PayslipStorageError, PayslipUploadStore
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["documents"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/documents/new", response_class=HTMLResponse)
async def new_document_upload(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    settings = request.app.state.settings
    client_config = {
        "max_files": MAX_QUEUE_FILES,
        "endpoint": f"/workspaces/{workspace.id}/document-uploads",
        "categories": client_catalog(
            max_csv_bytes=settings.max_csv_upload_bytes,
            max_payslip_bytes=settings.max_payslip_upload_bytes,
        ),
    }
    return templates.TemplateResponse(
        request=request,
        name="documents/upload.html",
        context={
            "request": request,
            "current_user": user,
            "workspace": workspace,
            "csrf_token": request.state.csrf_token,
            "client_config": client_config,
        },
    )


async def _multipart_file_count(request: Request) -> int:
    form = await request.form()
    return sum(
        isinstance(value, StarletteUploadFile) for _, value in form.multi_items()
    )


def _error(
    code: str,
    message: str,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "code": code, "message": message},
    )


def _process_csv(
    session: Session,
    store: LocalUploadStore,
    workspace: Workspace,
    document: UploadFile,
    retention_choice: str,
) -> DocumentProcessResult:
    result = create_csv_import(session, store, workspace, document.file, retention_choice)
    if result.kind == "already_committed":
        destination = f"/workspaces/{workspace.id}/transactions?already_imported=1"
        label = "View transactions"
    elif result.job.status == "reviewing":
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/review"
        label = "Review transactions"
    else:
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/mapping"
        label = "Map columns"
    return DocumentProcessResult("Ready for review.", destination, label)


def _process_payslip(
    session: Session,
    store: PayslipUploadStore,
    extractor: DocumentExtractor,
    workspace: Workspace,
    document: UploadFile,
    retention_choice: str,
) -> DocumentProcessResult:
    suffix = Path(document.filename or "").suffix.casefold()
    payslip = create_payslip_import(
        session,
        store,
        extractor,
        workspace,
        document.file,
        suffix,
        retention_choice,
    )
    return DocumentProcessResult(
        "Ready for review.",
        f"/workspaces/{workspace.id}/payslips/{payslip.id}/review",
        "Review payslip",
    )


@router.post("/document-uploads", dependencies=[Depends(require_csrf)])
def process_document_upload(
    request: Request,
    multipart_file_count: Annotated[int, Depends(_multipart_file_count)],
    documents: Annotated[list[UploadFile], File(alias="document")],
    category_key: Annotated[str, Form()],
    retention_choice: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> JSONResponse:
    """Validate and dispatch one supported private document for review."""
    if multipart_file_count != 1 or len(documents) != 1:
        return _error(
            "invalid_file_count",
            "Upload exactly one document.",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    document = documents[0]
    try:
        category = validate_processable_upload(
            category_key,
            document.filename or "",
            document.content_type,
        )
        if category.processor == "csv_import":
            result = _process_csv(
                session,
                request.app.state.upload_store,
                workspace,
                document,
                retention_choice,
            )
        else:
            assert category.processor == "payslip"
            result = _process_payslip(
                session,
                request.app.state.payslip_store,
                request.app.state.payslip_extractor,
                workspace,
                document,
                retention_choice,
            )
    except DocumentUploadValidationError as exc:
        return _error(exc.code, exc.message)
    except (
        CsvValidationError,
        UploadStorageError,
        ImportStateError,
        DocumentExtractionError,
        PayslipStorageError,
        PayslipImportError,
    ) as exc:
        return _error(exc.code, exc.message)
    return JSONResponse(result.as_payload())
