from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts.service import AccountNotFoundError, get_workspace_account
from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import Account, AccountStatementImport, User, Workspace
from app.db.session import get_db
from app.statement_imports.service import (
    StatementImportError,
    confirm_statement_import,
    get_workspace_statement_import,
    ingest_one_statement,
    list_compatible_accounts,
    retry_statement_source_cleanup,
)
from app.statement_imports.storage import StatementStorageError, StatementUploadStore
from app.statement_imports.types import StatementFormatError, StatementReviewValidationError
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["statement imports"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
ACCOUNT_TYPE_TO_CATEGORY = {
    "checking": "bank_account",
    "savings": "bank_account",
    "credit_card": "credit_card",
    "investment_401k": "investment_401k",
    "investment_brokerage": "brokerage",
    "mortgage": "mortgage",
    "auto_loan": "loan",
    "student_loan": "loan",
    "other": "other",
}
CATEGORY_LABELS = {
    "bank_account": "Checking or savings",
    "credit_card": "Credit card",
    "investment_401k": "401(k)",
    "brokerage": "Brokerage",
    "mortgage": "Mortgage",
    "loan": "Loan",
    "other": "Other",
}
EXTRACTION_LABELS = {
    "wimm_csv": "WIMM balance CSV",
    "embedded_text": "Embedded PDF text",
    "ocr": "Local OCR",
}


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _context(request: Request, user: User, workspace: Workspace, **values: object):
    return {
        "request": request,
        "current_user": user,
        "csrf_token": request.state.csrf_token,
        "workspace": workspace,
        **values,
    }


def _store(request: Request) -> StatementUploadStore:
    return request.app.state.statement_store


def _extractor(request: Request):
    return request.app.state.statement_extractor


def _pending_or_404(
    session: Session, workspace: Workspace, statement_import_id: int
) -> AccountStatementImport:
    pending = get_workspace_statement_import(session, workspace.id, statement_import_id)
    if pending is None:
        raise HTTPException(status_code=404)
    return pending


def _candidate_values(pending: AccountStatementImport) -> dict[str, str]:
    fields = pending.candidate_fields
    cents = fields.get("balance_cents")
    amount = f"{cents // 100}.{cents % 100:02d}" if isinstance(cents, int) else ""
    return {
        "account_id": str(pending.account_id or ""),
        "account_name": str(fields.get("account_name") or ""),
        "institution": str(fields.get("institution") or ""),
        "account_last_four": str(fields.get("account_last_four") or ""),
        "total_balance": amount,
        "as_of_date": str(fields.get("as_of_date") or ""),
    }


def _review_page(
    request: Request,
    user: User,
    workspace: Workspace,
    pending: AccountStatementImport,
    accounts: tuple[Account, ...],
    *,
    values: dict[str, str] | None = None,
    field_errors: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="statement_imports/review.html",
        context=_context(
            request,
            user,
            workspace,
            pending=pending,
            accounts=accounts,
            values=values or _candidate_values(pending),
            field_errors=field_errors or {},
            error=error,
            today=_utc_today(),
            category_label=CATEGORY_LABELS.get(
                pending.statement_category, pending.statement_category
            ),
            extraction_label=EXTRACTION_LABELS.get(
                str(pending.candidate_fields.get("extraction_method")), "Local extraction"
            ),
        ),
        status_code=status_code,
    )


@router.get("/accounts/{account_id}/statements/new", response_class=HTMLResponse)
async def new_statement_import(
    request: Request,
    account_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        account = get_workspace_account(session, workspace.id, account_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=404) from None
    category = ACCOUNT_TYPE_TO_CATEGORY.get(account.account_type)
    if category is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="statement_imports/upload.html",
        context=_context(request, user, workspace, account=account, category=category, error=None),
    )


@router.post("/statement-imports", dependencies=[Depends(require_csrf)])
def upload_statement(
    request: Request,
    statement_file: Annotated[UploadFile, File()],
    statement_category: Annotated[str, Form()],
    retention_choice: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        pending = ingest_one_statement(
            session,
            _store(request),
            _extractor(request),
            workspace,
            statement_category,
            statement_file.filename or "",
            statement_file.content_type or "",
            statement_file.file,
            retention_choice,
        )
    except (StatementImportError, StatementFormatError, StatementStorageError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="statement_imports/upload.html",
            context=_context(
                request,
                user,
                workspace,
                account=None,
                category=statement_category,
                error=str(exc),
            ),
            status_code=400,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/statement-imports/{pending.id}/review", status_code=303
    )


@router.get("/statement-imports/{statement_import_id}/review", response_class=HTMLResponse)
async def review_statement(
    request: Request,
    statement_import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    pending = _pending_or_404(session, workspace, statement_import_id)
    if pending.review_status.startswith("confirmed"):
        return RedirectResponse(f"/workspaces/{workspace.id}/dashboard", status_code=303)
    accounts = list_compatible_accounts(session, workspace.id, pending.statement_category)
    return _review_page(request, user, workspace, pending, accounts)


@router.post(
    "/statement-imports/{statement_import_id}/confirm", dependencies=[Depends(require_csrf)]
)
async def confirm_statement(
    request: Request,
    statement_import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    pending = _pending_or_404(session, workspace, statement_import_id)
    form = await request.form()
    values = {key: str(value) for key, value in form.items() if key != "csrf_token"}
    accounts = list_compatible_accounts(session, workspace.id, pending.statement_category)
    try:
        result = confirm_statement_import(
            session, _store(request), pending, values, today=_utc_today()
        )
    except StatementReviewValidationError as exc:
        return _review_page(
            request,
            user,
            workspace,
            pending,
            accounts,
            values=values,
            field_errors=exc.field_errors,
            error=str(exc),
            status_code=400,
        )
    except StatementImportError as exc:
        if exc.code == "account_not_found":
            raise HTTPException(status_code=404) from None
        return _review_page(
            request,
            user,
            workspace,
            pending,
            accounts,
            values=values,
            error=str(exc),
            status_code=409,
        )
    destination = f"/workspaces/{workspace.id}/dashboard"
    if result.cleanup_failed:
        destination += f"?statement_cleanup_failed={pending.id}"
    return RedirectResponse(destination, status_code=303)


@router.post(
    "/statement-imports/{statement_import_id}/cleanup",
    dependencies=[Depends(require_csrf)],
)
async def retry_statement_cleanup(
    request: Request,
    statement_import_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    del user
    pending = _pending_or_404(session, workspace, statement_import_id)
    try:
        retry_statement_source_cleanup(session, _store(request), pending)
    except StatementImportError as exc:
        if exc.code != "cleanup_failed":
            raise HTTPException(status_code=404) from None
        return RedirectResponse(
            f"/workspaces/{workspace.id}/dashboard?statement_cleanup_failed={pending.id}",
            status_code=303,
        )
    return RedirectResponse(f"/workspaces/{workspace.id}/dashboard", status_code=303)
