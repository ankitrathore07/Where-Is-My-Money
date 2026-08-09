"""Authenticated workspace transaction list route."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.db.models import Category, User, Workspace
from app.db.session import get_db
from app.transactions.queries import (
    FilterValidationError,
    TransactionFilters,
    TransactionPage,
    list_transactions,
    parse_filters,
)
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["transactions"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def _categories(session: Session, workspace_id: int) -> tuple[Category, ...]:
    return tuple(
        session.scalars(
            select(Category)
            .where(or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id))
            .order_by(Category.kind, Category.name)
        )
    )


def _query_for_page(filters: TransactionFilters, page: int) -> str:
    values: dict[str, str | int] = {}
    if filters.start_date is not None:
        values["start_date"] = filters.start_date.isoformat()
    if filters.end_date is not None:
        values["end_date"] = filters.end_date.isoformat()
    if filters.category_id is not None:
        values["category_id"] = filters.category_id
    if filters.direction != "all":
        values["direction"] = filters.direction
    if filters.query:
        values["q"] = filters.query
    values["page"] = page
    return urlencode(values)


def _format_money(cents: int) -> str:
    return f"${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def _filter_values(request: Request) -> dict[str, str]:
    return {
        field: request.query_params.get(field, "")
        for field in ("start_date", "end_date", "category_id", "direction", "q")
    }


@router.get("/transactions", response_class=HTMLResponse, name="transaction_list")
async def transaction_list(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Render one authorized, filtered page of transactions."""
    errors: dict[str, str] = {}
    filters: TransactionFilters | None = None
    page: TransactionPage | None = None
    try:
        filters = parse_filters(request.query_params)
        page = list_transactions(session, workspace.id, filters)
    except FilterValidationError as exc:
        errors = exc.field_errors

    previous_query = None
    next_query = None
    if filters is not None and page is not None:
        if page.page > 1:
            previous_query = _query_for_page(filters, page.page - 1)
        if page.page < page.total_pages:
            next_query = _query_for_page(filters, page.page + 1)

    return templates.TemplateResponse(
        request=request,
        name="transactions/list.html",
        context={
            "request": request,
            "current_user": user,
            "csrf_token": request.state.csrf_token,
            "workspace": workspace,
            "categories": _categories(session, workspace.id),
            "page": page,
            "errors": errors,
            "filter_values": _filter_values(request),
            "previous_query": previous_query,
            "next_query": next_query,
            "format_money": _format_money,
            "already_imported": request.query_params.get("already_imported") == "1",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if errors else status.HTTP_200_OK,
    )
