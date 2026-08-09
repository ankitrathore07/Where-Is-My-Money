"""FastAPI dependencies for signed-session authentication."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db


def get_optional_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> User | None:
    """Resolve the signed session's user without trusting malformed values."""
    user_id = request.session.get("user_id")
    if type(user_id) is not int:
        return None
    return session.get(User, user_id)


def require_current_user(
    user: Annotated[User | None, Depends(get_optional_current_user)],
) -> User:
    """Require authentication before protected route work begins."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/"},
        )
    return user
