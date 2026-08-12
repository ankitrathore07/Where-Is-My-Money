"""Authorized server-rendered routes for workspace financial dashboards."""

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.types import ACCOUNT_TYPE_OPTIONS
from app.auth.dependencies import require_current_user
from app.dashboard.presentation import (
    chart_payload,
    dashboard_page_data,
    format_basis_points,
    format_money,
)
from app.dashboard.service import build_dashboard_report
from app.db.models import AccountStatementImport, User, Workspace
from app.db.session import get_db
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["dashboard"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
_ACCOUNT_TYPE_LABELS = {option.value: option.label for option in ACCOUNT_TYPE_OPTIONS}


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


def _parse_as_of_date(value: str) -> date | None:
    if not value:
        return None
    if (
        len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or not value.replace("-", "").isdigit()
    ):
        raise ValueError
    return date.fromisoformat(value)


def _date_error_response(
    request: Request, user: User, workspace: Workspace, error: str
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context=_context(
            request,
            user,
            workspace,
            error=error,
            report=None,
            page_data=None,
            chart_data=None,
            format_money=format_money,
            format_basis_points=format_basis_points,
            account_type_labels=_ACCOUNT_TYPE_LABELS,
        ),
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


@router.get("/dashboard", response_class=HTMLResponse, name="dashboard")
async def dashboard(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    as_of: str = "",
    statement_cleanup_failed: int | None = None,
) -> HTMLResponse:
    """Render the active member's aggregate financial dashboard."""
    try:
        as_of_date = _parse_as_of_date(as_of)
    except ValueError:
        return _date_error_response(
            request, user, workspace, "Use a valid date in YYYY-MM-DD format."
        )
    report = build_dashboard_report(session, workspace.id, as_of_date)
    cleanup_import = None
    if statement_cleanup_failed is not None:
        cleanup_import = session.scalar(
            select(AccountStatementImport).where(
                AccountStatementImport.id == statement_cleanup_failed,
                AccountStatementImport.workspace_id == workspace.id,
                AccountStatementImport.review_status == "confirmed_cleanup_failed",
            )
        )
    return templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context=_context(
            request,
            user,
            workspace,
            report=report,
            page_data=dashboard_page_data(report),
            chart_data=chart_payload(report),
            format_money=format_money,
            format_basis_points=format_basis_points,
            account_type_labels=_ACCOUNT_TYPE_LABELS,
            statement_cleanup_import=cleanup_import,
        ),
    )
