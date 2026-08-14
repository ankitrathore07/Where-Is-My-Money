"""Authenticated management routes for workspace tags."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import User, Workspace
from app.db.session import get_db
from app.tags.service import (
    BuiltinTagMutationError,
    DuplicateTagNameError,
    TagNotFoundError,
    TagValidationError,
    create_custom_tag,
    delete_custom_tag,
    list_accessible_tags,
    rename_custom_tag,
)
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["tags"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def _page(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="tags/index.html",
        context={
            "request": request,
            "current_user": user,
            "csrf_token": request.state.csrf_token,
            "workspace": workspace,
            "choices": list_accessible_tags(session, workspace.id),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/tags", response_class=HTMLResponse)
async def tag_list(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return _page(request, user, session, workspace)


@router.post("/tags", dependencies=[Depends(require_csrf)])
async def tag_create(
    request: Request,
    name: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        create_custom_tag(session, workspace.id, name)
        session.commit()
    except (TagValidationError, DuplicateTagNameError) as exc:
        session.rollback()
        return _page(request, user, session, workspace, error=str(exc), status_code=422)
    return RedirectResponse(f"/workspaces/{workspace.id}/tags", status_code=303)


@router.post("/tags/inline", dependencies=[Depends(require_csrf)])
async def tag_create_inline(
    name: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> JSONResponse:
    """Create a workspace tag from an explicit import-review action."""
    del user
    try:
        tag = create_custom_tag(session, workspace.id, name)
        session.commit()
    except DuplicateTagNameError as exc:
        session.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)
    except TagValidationError as exc:
        session.rollback()
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    return JSONResponse({"id": tag.id, "name": tag.name}, status_code=status.HTTP_201_CREATED)


@router.post("/tags/{tag_id}", dependencies=[Depends(require_csrf)])
async def tag_rename(
    request: Request,
    tag_id: int,
    name: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        rename_custom_tag(session, workspace.id, tag_id, name)
        session.commit()
    except TagNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404) from exc
    except (BuiltinTagMutationError, TagValidationError, DuplicateTagNameError) as exc:
        session.rollback()
        return _page(request, user, session, workspace, error=str(exc), status_code=422)
    return RedirectResponse(f"/workspaces/{workspace.id}/tags", status_code=303)


@router.post("/tags/{tag_id}/delete", dependencies=[Depends(require_csrf)])
async def tag_delete(
    tag_id: int,
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    try:
        delete_custom_tag(session, workspace.id, tag_id)
        session.commit()
    except TagNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404) from exc
    except BuiltinTagMutationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/tags", status_code=303)
