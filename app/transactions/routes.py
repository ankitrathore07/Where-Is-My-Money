"""Authenticated workspace transaction list route."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.categories.service import list_accessible_categories
from app.core.middleware import require_csrf
from app.db.models import Category, User, Workspace
from app.db.session import get_db
from app.transactions.queries import (
    FilterValidationError,
    TransactionFilters,
    TransactionPage,
    list_transactions,
    parse_filters,
)
from app.transactions.service import (
    CategoryNotAccessibleError,
    ManualCategorizationInput,
    ManualCategorizationValidationError,
    MerchantRuleKeyError,
    TransactionNotFoundError,
    get_transaction_for_categorization,
    manually_categorize_transaction,
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
    if filters.subscription != "all":
        values["subscription"] = filters.subscription
    if filters.query:
        values["q"] = filters.query
    values["page"] = page
    return urlencode(values)


def _format_money(cents: int) -> str:
    return f"${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def _filter_values(request: Request) -> dict[str, str]:
    return {
        field: request.query_params.get(field, "")
        for field in (
            "start_date",
            "end_date",
            "category_id",
            "direction",
            "subscription",
            "q",
        )
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


def _categorization_response(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    transaction_id: int,
    *,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    submitted_merchant: str | None = None,
    submitted_category_id: int | None = None,
    submitted_subscription: bool | None = None,
    submitted_save_for_future: bool = False,
) -> HTMLResponse:
    try:
        transaction = get_transaction_for_categorization(session, workspace.id, transaction_id)
    except TransactionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    return templates.TemplateResponse(
        request=request,
        name="transactions/edit.html",
        context={
            "request": request,
            "current_user": user,
            "csrf_token": request.state.csrf_token,
            "workspace": workspace,
            "transaction": transaction,
            "choices": list_accessible_categories(session, workspace.id),
            "error": error,
            "merchant_value": submitted_merchant
            if submitted_merchant is not None
            else transaction.normalized_merchant or transaction.description,
            "category_value": submitted_category_id
            if submitted_category_id is not None
            else transaction.category_id,
            "subscription_value": submitted_subscription
            if submitted_subscription is not None
            else transaction.is_subscription,
            "save_for_future_value": submitted_save_for_future,
        },
        status_code=status_code,
    )


@router.get(
    "/transactions/{transaction_id}/categorization",
    response_class=HTMLResponse,
)
async def transaction_categorization_form(
    transaction_id: int,
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return _categorization_response(request, user, session, workspace, transaction_id)


@router.post(
    "/transactions/{transaction_id}/categorization",
    dependencies=[Depends(require_csrf)],
)
async def transaction_categorization_submit(
    transaction_id: int,
    request: Request,
    normalized_merchant: Annotated[str, Form()],
    category_id: Annotated[int, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    is_subscription: Annotated[str | None, Form()] = None,
    save_for_future: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    subscription_value = is_subscription is not None
    save_value = save_for_future is not None
    try:
        manually_categorize_transaction(
            session,
            workspace.id,
            transaction_id,
            ManualCategorizationInput(
                normalized_merchant,
                category_id,
                subscription_value,
                save_value,
            ),
        )
        session.commit()
    except (TransactionNotFoundError, CategoryNotAccessibleError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    except (ManualCategorizationValidationError, MerchantRuleKeyError) as exc:
        session.rollback()
        return _categorization_response(
            request,
            user,
            session,
            workspace,
            transaction_id,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error=str(exc),
            submitted_merchant=normalized_merchant,
            submitted_category_id=category_id,
            submitted_subscription=subscription_value,
            submitted_save_for_future=save_value,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/transactions",
        status_code=status.HTTP_303_SEE_OTHER,
    )
