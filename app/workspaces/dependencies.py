"""FastAPI authorization dependencies for workspace-scoped routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.db.models import User, Workspace
from app.db.session import get_db
from app.workspaces.service import get_authorized_workspace


def require_workspace(
    workspace_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> Workspace:
    """Resolve a path workspace only through the current user's membership."""
    workspace = get_authorized_workspace(session, user.id, workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
    return workspace
