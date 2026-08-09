"""Workspace membership and invitation business rules."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import normalize_email
from app.core.security import generate_invite_token, hash_invitation_token
from app.db.models import User, Workspace, WorkspaceInvitation, WorkspaceMembership


class WorkspaceRuleError(ValueError):
    """Raised when a workspace operation violates a sharing rule."""


class InvitationError(ValueError):
    """Raised when an invitation is invalid or cannot be used."""


@dataclass(frozen=True)
class InvitationDispatch:
    """A stored invitation paired with its one-time bearer token."""

    invitation: WorkspaceInvitation
    raw_token: str


def create_household_workspace(session: Session, user: User, name: str) -> Workspace:
    """Create a shared workspace and make its creator an equal member."""
    normalized_name = name.strip()
    if not normalized_name or len(normalized_name) > 255:
        raise WorkspaceRuleError("Workspace name must be between 1 and 255 characters")

    workspace = Workspace(name=normalized_name, is_personal=False, owner_id=user.id)
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="member"))
    session.flush()
    return workspace


def list_user_workspaces(session: Session, user_id: int) -> list[Workspace]:
    """List only workspaces for which the user has a membership."""
    return list(
        session.scalars(
            select(Workspace)
            .join(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user_id)
            .order_by(Workspace.is_personal.desc(), Workspace.id)
        )
    )


def get_authorized_workspace(session: Session, user_id: int, workspace_id: int) -> Workspace | None:
    """Load a workspace through membership, never through ownership alone."""
    return session.scalar(
        select(Workspace)
        .join(WorkspaceMembership)
        .where(
            Workspace.id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )


def create_workspace_invitation(
    session: Session,
    workspace: Workspace,
    inviter: User,
    email: str,
    *,
    now: datetime | None = None,
) -> InvitationDispatch:
    """Create a pending invitation after enforcing household membership rules."""
    current_time = now or datetime.now(UTC)
    normalized_email = normalize_email(email)
    if workspace.is_personal:
        raise WorkspaceRuleError("Personal workspaces cannot be shared")
    if not normalized_email or len(normalized_email) > 320:
        raise InvitationError("Enter a valid invitation email")
    try:
        validate_email(normalized_email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvitationError("Enter a valid invitation email") from exc
    if get_authorized_workspace(session, inviter.id, workspace.id) is None:
        raise WorkspaceRuleError("Workspace not found")

    existing_member = session.scalar(
        select(User)
        .join(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace.id,
            func.lower(User.email) == normalized_email,
        )
    )
    if existing_member is not None:
        raise InvitationError("That person is already a member")

    pending_invitations = session.scalars(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace.id,
            WorkspaceInvitation.email == normalized_email,
            WorkspaceInvitation.accepted.is_(False),
        )
    )
    if any(
        not _is_expired(invitation.expires_at, current_time) for invitation in pending_invitations
    ):
        raise InvitationError("A pending invitation already exists for that email")

    raw_token = generate_invite_token()
    invitation = WorkspaceInvitation(
        workspace_id=workspace.id,
        email=normalized_email,
        token=hash_invitation_token(raw_token),
        invited_by_id=inviter.id,
        accepted=False,
        expires_at=current_time + timedelta(days=7),
    )
    session.add(invitation)
    session.flush()
    return InvitationDispatch(invitation=invitation, raw_token=raw_token)


def accept_workspace_invitation(
    session: Session,
    user: User,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> Workspace:
    """Accept one live invitation for the matching verified user email."""
    current_time = now or datetime.now(UTC)
    invitation = session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token == hash_invitation_token(raw_token)
        )
    )
    if (
        invitation is None
        or invitation.accepted
        or _is_expired(invitation.expires_at, current_time)
        or normalize_email(user.email) != invitation.email
    ):
        raise InvitationError("Invitation is invalid or unavailable")

    existing_membership = session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == invitation.workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if existing_membership is not None:
        raise InvitationError("Invitation is invalid or unavailable")

    session.add(
        WorkspaceMembership(
            workspace_id=invitation.workspace_id,
            user_id=user.id,
            role="member",
        )
    )
    invitation.accepted = True
    session.flush()
    return invitation.workspace


def get_pending_invitation(
    session: Session,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> WorkspaceInvitation | None:
    """Load one live, unaccepted invitation by its bearer token."""
    invitation = session.scalar(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.token == hash_invitation_token(raw_token)
        )
    )
    if (
        invitation is None
        or invitation.accepted
        or _is_expired(invitation.expires_at, now or datetime.now(UTC))
    ):
        return None
    return invitation


def list_pending_invitations_for_email(
    session: Session,
    email: str,
    *,
    now: datetime | None = None,
) -> list[WorkspaceInvitation]:
    """List live invitations addressed to one normalized verified email."""
    current_time = now or datetime.now(UTC)
    invitations = session.scalars(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.email == normalize_email(email),
            WorkspaceInvitation.accepted.is_(False),
        )
        .order_by(WorkspaceInvitation.id)
    )
    return [
        invitation
        for invitation in invitations
        if not _is_expired(invitation.expires_at, current_time)
    ]


def list_workspace_pending_invitations(
    session: Session,
    workspace_id: int,
    *,
    now: datetime | None = None,
) -> list[WorkspaceInvitation]:
    """List live pending invitations for an authorized workspace page."""
    current_time = now or datetime.now(UTC)
    invitations = session.scalars(
        select(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.accepted.is_(False),
        )
        .order_by(WorkspaceInvitation.id)
    )
    return [
        invitation
        for invitation in invitations
        if not _is_expired(invitation.expires_at, current_time)
    ]


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return True
    comparable_expiry = (
        expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    )
    comparable_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return comparable_expiry <= comparable_now
