"""Authorized server-rendered custom category routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.categories.service import (
    CategoryValidationError,
    DuplicateCategoryNameError,
    create_custom_category,
    list_accessible_categories,
)
from app.core.middleware import require_csrf
from app.db.models import User, Workspace
from app.db.session import get_db
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["categories"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def _render(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    status_code: int = status.HTTP_200_OK,
    error: CategoryValidationError | None = None,
    submitted_name: str = "",
    submitted_kind: str = "expense",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="categories/index.html",
        context={
            "request": request,
            "current_user": user,
            "csrf_token": request.state.csrf_token,
            "workspace": workspace,
            "choices": list_accessible_categories(session, workspace.id),
            "error": error,
            "submitted_name": submitted_name,
            "submitted_kind": submitted_kind,
        },
        status_code=status_code,
    )


@router.get("/categories", response_class=HTMLResponse)
async def category_list(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return _render(request, user, session, workspace)


@router.post("/categories", dependencies=[Depends(require_csrf)])
async def category_create(
    request: Request,
    name: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        create_custom_category(session, workspace.id, name, kind)
        session.commit()
    except (CategoryValidationError, DuplicateCategoryNameError) as exc:
        session.rollback()
        return _render(
            request,
            user,
            session,
            workspace,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error=exc,
            submitted_name=name,
            submitted_kind=kind,
        )
    except IntegrityError:
        session.rollback()
        return _render(
            request,
            user,
            session,
            workspace,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error=DuplicateCategoryNameError(),
            submitted_name=name,
            submitted_kind=kind,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/categories",
        status_code=status.HTTP_303_SEE_OTHER,
    )
