"""Authorized server-rendered account and manual balance routes."""

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts.service import (
    AccountNotFoundError,
    AccountValidationError,
    add_manual_balance,
    create_account,
    get_workspace_account,
    list_workspace_accounts,
    update_account,
)
from app.accounts.types import ACCOUNT_TYPE_OPTIONS, AccountInput, ManualBalanceInput
from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import Account, User, Workspace
from app.db.session import get_db
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["accounts"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


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


def _account_values(account: Account | None = None) -> dict[str, str]:
    if account is None:
        return {
            "name": "",
            "account_type": "checking",
            "institution": "",
            "classification": "asset",
        }
    return {
        "name": account.name,
        "account_type": account.account_type,
        "institution": account.institution or "",
        "classification": "liability" if account.is_liability else "asset",
    }


def _render_account_form(
    request: Request,
    user: User,
    workspace: Workspace,
    *,
    account: Account | None = None,
    values: dict[str, str] | None = None,
    field_errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="accounts/form.html",
        context=_context(
            request,
            user,
            workspace,
            account=account,
            values=values or _account_values(account),
            field_errors=field_errors or {},
            account_type_options=ACCOUNT_TYPE_OPTIONS,
        ),
        status_code=status_code,
    )


def _classification_is_liability(classification: str) -> bool:
    if classification not in {"asset", "liability"}:
        raise AccountValidationError({"classification": "Choose asset or liability."})
    return classification == "liability"


@router.get("/accounts", response_class=HTMLResponse)
async def account_list(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="accounts/index.html",
        context=_context(
            request,
            user,
            workspace,
            accounts=list_workspace_accounts(session, workspace.id),
        ),
    )


@router.get("/accounts/new", response_class=HTMLResponse)
async def new_account(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    del session
    return _render_account_form(request, user, workspace)


@router.post("/accounts", dependencies=[Depends(require_csrf)])
async def account_create(
    request: Request,
    name: Annotated[str, Form()],
    account_type: Annotated[str, Form()],
    institution: Annotated[str, Form()],
    classification: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    values = {
        "name": name,
        "account_type": account_type,
        "institution": institution,
        "classification": classification,
    }
    try:
        create_account(
            session,
            workspace.id,
            AccountInput(
                name, account_type, institution, _classification_is_liability(classification)
            ),
        )
        session.commit()
    except AccountValidationError as exc:
        session.rollback()
        return _render_account_form(
            request,
            user,
            workspace,
            values=values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/accounts", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
async def edit_account(
    request: Request,
    account_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        account = get_workspace_account(session, workspace.id, account_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    return _render_account_form(request, user, workspace, account=account)


@router.post("/accounts/{account_id}", dependencies=[Depends(require_csrf)])
async def account_update(
    request: Request,
    account_id: int,
    name: Annotated[str, Form()],
    account_type: Annotated[str, Form()],
    institution: Annotated[str, Form()],
    classification: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    values = {
        "name": name,
        "account_type": account_type,
        "institution": institution,
        "classification": classification,
    }
    try:
        update_account(
            session,
            workspace.id,
            account_id,
            AccountInput(
                name, account_type, institution, _classification_is_liability(classification)
            ),
        )
        session.commit()
    except AccountNotFoundError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    except AccountValidationError as exc:
        session.rollback()
        return _render_account_form(
            request,
            user,
            workspace,
            values=values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/accounts", status_code=status.HTTP_303_SEE_OTHER
    )


def _render_balance_form(
    request: Request,
    user: User,
    workspace: Workspace,
    account: Account,
    *,
    today: date,
    values: dict[str, str] | None = None,
    field_errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="accounts/balance_form.html",
        context=_context(
            request,
            user,
            workspace,
            account=account,
            today=today,
            values=values or {"amount": "", "as_of_date": today.isoformat()},
            field_errors=field_errors or {},
        ),
        status_code=status_code,
    )


@router.get("/accounts/{account_id}/balances/new", response_class=HTMLResponse)
async def new_manual_balance(
    request: Request,
    account_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        account = get_workspace_account(session, workspace.id, account_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    return _render_balance_form(request, user, workspace, account, today=date.today())


@router.post("/accounts/{account_id}/balances", dependencies=[Depends(require_csrf)])
async def manual_balance_create(
    request: Request,
    account_id: int,
    amount: Annotated[str, Form()],
    as_of_date: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    today = date.today()
    values = {"amount": amount, "as_of_date": as_of_date}
    try:
        add_manual_balance(
            session,
            workspace.id,
            account_id,
            ManualBalanceInput(amount, as_of_date),
            today=today,
        )
        session.commit()
    except AccountNotFoundError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    except AccountValidationError as exc:
        session.rollback()
        try:
            account = get_workspace_account(session, workspace.id, account_id)
        except AccountNotFoundError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        return _render_balance_form(
            request,
            user,
            workspace,
            account,
            today=today,
            values=values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/accounts", status_code=status.HTTP_303_SEE_OTHER
    )
