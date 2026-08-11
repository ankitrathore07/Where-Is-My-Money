"""Authenticated, workspace-scoped payslip import and income pages."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import Payslip, User, Workspace
from app.db.session import get_db
from app.payslips.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    TesseractOcrEngine,
)
from app.payslips.parsing import ReviewValidationError
from app.payslips.service import (
    PayslipImportError,
    confirm_payslip,
    create_payslip_import,
    get_income_summary,
    get_workspace_payslip,
)
from app.payslips.storage import PayslipStorageError, PayslipUploadStore
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["payslips"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
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


def _store(request: Request) -> PayslipUploadStore:
    configured = request.app.state.settings
    return getattr(
        request.app.state,
        "payslip_store",
        PayslipUploadStore(
            configured.upload_directory,
            configured.max_payslip_upload_bytes,
        ),
    )


def _extractor(request: Request) -> DocumentExtractor:
    return getattr(
        request.app.state,
        "payslip_extractor",
        DocumentExtractor(TesseractOcrEngine()),
    )


def _payslip_or_404(session: Session, workspace: Workspace, payslip_id: int) -> Payslip:
    payslip = get_workspace_payslip(session, workspace.id, payslip_id)
    if payslip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return payslip


def _format_cents(value: object) -> str:
    if not isinstance(value, int):
        return ""
    return f"{value // 100}.{value % 100:02d}"


def _format_money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    absolute = abs(cents)
    return f"{sign}${absolute // 100:,}.{absolute % 100:02d}"


def _candidate_values(payslip: Payslip) -> dict[str, str]:
    candidates = payslip.candidate_fields if isinstance(payslip.candidate_fields, dict) else {}
    return {
        "employer": str(candidates.get("employer") or ""),
        "pay_period_start": str(candidates.get("pay_period_start") or ""),
        "pay_period_end": str(candidates.get("pay_period_end") or ""),
        "pay_date": str(candidates.get("pay_date") or ""),
        "gross_pay": _format_cents(candidates.get("gross_pay_cents")),
        "net_pay": _format_cents(candidates.get("net_pay_cents")),
        "taxes": _format_cents(candidates.get("taxes_cents")),
        "deductions": _format_cents(candidates.get("deductions_cents")),
    }


def _review_page(
    request: Request,
    user: User,
    workspace: Workspace,
    payslip: Payslip,
    *,
    values: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    candidates = payslip.candidate_fields if isinstance(payslip.candidate_fields, dict) else {}
    extraction_labels = {
        "ocr": "Local OCR",
        "embedded_text": "Embedded PDF text",
    }
    return templates.TemplateResponse(
        request=request,
        name="payslips/review.html",
        context=_context(
            request,
            user,
            workspace,
            payslip=payslip,
            values=values or _candidate_values(payslip),
            errors=errors or {},
            error=error,
            extraction_label=extraction_labels.get(
                candidates.get("extraction_method"), "Manual review"
            ),
        ),
        status_code=status_code,
    )


def _upload_page(
    request: Request,
    user: User,
    workspace: Workspace,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="payslips/upload.html",
        context=_context(request, user, workspace, error=error),
        status_code=status_code,
    )


@router.get("/payslips/new", response_class=HTMLResponse)
async def new_payslip(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Show the private PDF/image payslip upload form."""
    return _upload_page(request, user, workspace)


@router.post("/payslips", dependencies=[Depends(require_csrf)])
def upload_payslip(
    request: Request,
    payslip_file: Annotated[UploadFile, File()],
    retention_choice: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Validate, privately store, and extract one payslip for review."""
    suffix = Path(payslip_file.filename or "").suffix.casefold()
    allowed_content_types = ALLOWED_CONTENT_TYPES.get(suffix)
    if (
        allowed_content_types is None
        or not payslip_file.content_type
        or payslip_file.content_type.casefold() not in allowed_content_types
    ):
        return _upload_page(
            request,
            user,
            workspace,
            error="Choose a PDF, PNG, or JPEG payslip.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        payslip = create_payslip_import(
            session,
            _store(request),
            _extractor(request),
            workspace,
            payslip_file.file,
            suffix,
            retention_choice,
        )
    except (DocumentExtractionError, PayslipStorageError, PayslipImportError) as exc:
        return _upload_page(
            request,
            user,
            workspace,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/payslips/{payslip.id}/review",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/payslips/{payslip_id}/review", response_class=HTMLResponse)
async def review_payslip(
    request: Request,
    payslip_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Show editable extracted candidates without creating income."""
    payslip = _payslip_or_404(session, workspace, payslip_id)
    if payslip.review_status.startswith("confirmed"):
        return RedirectResponse(
            f"/workspaces/{workspace.id}/income",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return _review_page(request, user, workspace, payslip)


@router.post(
    "/payslips/{payslip_id}/confirm",
    dependencies=[Depends(require_csrf)],
)
async def confirm_payslip_review(
    request: Request,
    payslip_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Create confirmed income from the exact editable form values."""
    payslip = _payslip_or_404(session, workspace, payslip_id)
    form = await request.form()
    values = {key: str(value) for key, value in form.items() if key != "csrf_token"}
    try:
        result = confirm_payslip(session, _store(request), payslip, values)
    except ReviewValidationError as exc:
        return _review_page(
            request,
            user,
            workspace,
            payslip,
            values=values,
            errors=exc.field_errors,
            error=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except PayslipImportError as exc:
        return _review_page(
            request,
            user,
            workspace,
            payslip,
            values=values,
            error=str(exc),
            status_code=status.HTTP_409_CONFLICT,
        )
    destination = f"/workspaces/{workspace.id}/income"
    if result.cleanup_failed:
        destination += "?cleanup_failed=1"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/income", response_class=HTMLResponse)
async def income_summary(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    cleanup_failed: bool = False,
) -> HTMLResponse:
    """Show confirmed gross/net totals for only the authorized workspace."""
    return templates.TemplateResponse(
        request=request,
        name="payslips/income.html",
        context=_context(
            request,
            user,
            workspace,
            summary=get_income_summary(session, workspace.id),
            cleanup_failed=cleanup_failed,
            format_money=_format_money,
        ),
    )
