"""Server-rendered personal and household workspace routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_optional_current_user, require_current_user
from app.auth.service import normalize_email
from app.core.middleware import require_csrf
from app.db.models import User, Workspace, WorkspaceMembership
from app.db.session import get_db
from app.workspaces.dependencies import require_workspace
from app.workspaces.service import (
    InvitationError,
    WorkspaceRuleError,
    accept_workspace_invitation,
    create_household_workspace,
    create_workspace_invitation,
    get_pending_invitation,
    list_pending_invitations_for_email,
    list_user_workspaces,
    list_workspace_pending_invitations,
)

router = APIRouter(tags=["workspaces"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def _context(request: Request, user: User | None, **values: object) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "csrf_token": request.state.csrf_token,
        **values,
    }


def _workspace_page_values(session: Session, workspace: Workspace) -> dict[str, object]:
    members = list(
        session.scalars(
            select(User)
            .join(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == workspace.id)
            .order_by(User.display_name, User.email)
        )
    )
    return {
        "workspace": workspace,
        "members": members,
        "pending_invitations": list_workspace_pending_invitations(session, workspace.id),
    }


@router.get("/workspaces", response_class=HTMLResponse)
async def workspace_list(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Show only memberships and invitations for the signed-in user."""
    return templates.TemplateResponse(
        request=request,
        name="workspaces.html",
        context=_context(
            request,
            user,
            workspaces=list_user_workspaces(session, user.id),
            pending_invitations=list_pending_invitations_for_email(session, user.email),
        ),
    )


@router.post("/workspaces", dependencies=[Depends(require_csrf)])
async def create_workspace(
    request: Request,
    name: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Create a household workspace and redirect to its authorized page."""
    try:
        workspace = create_household_workspace(session, user, name)
        session.commit()
    except WorkspaceRuleError as exc:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="workspaces.html",
            context=_context(
                request,
                user,
                workspaces=list_user_workspaces(session, user.id),
                pending_invitations=list_pending_invitations_for_email(session, user.email),
                error=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/workspaces/{workspace_id}", response_class=HTMLResponse)
async def workspace_detail(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Render one workspace only after membership authorization."""
    return templates.TemplateResponse(
        request=request,
        name="workspace_detail.html",
        context=_context(request, user, **_workspace_page_values(session, workspace)),
    )


@router.post(
    "/workspaces/{workspace_id}/invitations",
    dependencies=[Depends(require_csrf)],
)
async def invite_to_workspace(
    request: Request,
    email: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Create a pending email invitation as any household member."""
    try:
        dispatch = create_workspace_invitation(session, workspace, user, email)
        session.commit()
    except (WorkspaceRuleError, InvitationError) as exc:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="workspace_detail.html",
            context=_context(
                request,
                user,
                **_workspace_page_values(session, workspace),
                error=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return templates.TemplateResponse(
        request=request,
        name="workspace_detail.html",
        context=_context(
            request,
            user,
            **_workspace_page_values(session, workspace),
            invitation_url=f"/invitations/{dispatch.raw_token}",
        ),
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/invitations/{token}", response_class=HTMLResponse)
async def view_invitation(
    request: Request,
    token: str,
    user: Annotated[User | None, Depends(get_optional_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Show generic invitation guidance, revealing details only on email match."""
    invitation = get_pending_invitation(session, token)
    if invitation is None:
        return templates.TemplateResponse(
            request=request,
            name="invitation.html",
            context=_context(request, user, available=False),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    matching_user = user is not None and normalize_email(user.email) == invitation.email
    return templates.TemplateResponse(
        request=request,
        name="invitation.html",
        context=_context(
            request,
            user,
            available=True,
            matching_user=matching_user,
            invitation=invitation if matching_user else None,
            invitation_token=token if matching_user else None,
        ),
    )


@router.post(
    "/invitations/{token}/accept",
    dependencies=[Depends(require_csrf)],
)
async def accept_invitation(
    request: Request,
    token: str,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Add membership only for the invited, verified identity."""
    try:
        workspace = accept_workspace_invitation(session, user, token)
        workspace_id = workspace.id
        session.commit()
    except InvitationError:
        session.rollback()
        return templates.TemplateResponse(
            request=request,
            name="invitation.html",
            context=_context(request, user, available=False, error="Invitation is unavailable"),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return RedirectResponse(
        f"/workspaces/{workspace_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
