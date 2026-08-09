"""Database rules for verified Google identities."""

from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import User, Workspace, WorkspaceMembership


class InvalidGoogleIdentity(ValueError):
    """Raised when Google claims do not contain a verified identity."""


class GoogleIdentityConflict(ValueError):
    """Raised when a verified email belongs to another Google subject."""


def normalize_email(email: str) -> str:
    """Normalize an identity email for comparison and storage."""
    return email.strip().casefold()


def get_or_create_google_user(session: Session, claims: Mapping[str, object]) -> User:
    """Resolve verified Google claims and provision their private workspace."""
    google_sub = claims.get("sub")
    raw_email = claims.get("email")
    if (
        not isinstance(google_sub, str)
        or not google_sub.strip()
        or not isinstance(raw_email, str)
        or not normalize_email(raw_email)
        or claims.get("email_verified") is not True
    ):
        raise InvalidGoogleIdentity("Google did not return a verified identity")

    google_sub = google_sub.strip()
    email = normalize_email(raw_email)
    raw_name = claims.get("name")
    display_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None

    user = session.scalar(select(User).where(User.google_sub == google_sub))
    conflicting_user = session.scalar(
        select(User).where(func.lower(User.email) == email, User.google_sub != google_sub)
    )
    if conflicting_user is not None:
        raise GoogleIdentityConflict("Verified email belongs to another account")

    if user is None:
        user = User(google_sub=google_sub, email=email, display_name=display_name)
        session.add(user)
        session.flush()
    else:
        user.email = email
        user.display_name = display_name

    _ensure_personal_workspace(session, user)
    return user


def _ensure_personal_workspace(session: Session, user: User) -> None:
    workspace = session.scalar(
        select(Workspace).where(
            Workspace.owner_id == user.id,
            Workspace.is_personal.is_(True),
        )
    )
    if workspace is None:
        workspace = Workspace(name="Personal", is_personal=True, owner_id=user.id)
        session.add(workspace)
        session.flush()

    membership = session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace.id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if membership is None:
        session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="member"))
        session.flush()
