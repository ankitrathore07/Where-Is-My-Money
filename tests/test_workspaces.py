from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import get_or_create_google_user
from app.core.security import hash_invitation_token
from app.db.models import User, WorkspaceInvitation, WorkspaceMembership
from app.workspaces.service import (
    InvitationError,
    WorkspaceRuleError,
    accept_workspace_invitation,
    create_household_workspace,
    create_workspace_invitation,
    get_authorized_workspace,
    list_user_workspaces,
)

NOW = datetime(2026, 8, 8, 12, tzinfo=UTC)


def create_user(session: Session, subject: str, email: str, name: str) -> User:
    user = get_or_create_google_user(
        session,
        {
            "sub": subject,
            "email": email,
            "email_verified": True,
            "name": name,
        },
    )
    session.commit()
    return user


def test_household_creator_is_an_equal_member(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")

    household = create_household_workspace(session, owner, "  Our Home  ")
    session.commit()

    membership = session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == household.id,
            WorkspaceMembership.user_id == owner.id,
        )
    )
    assert household.name == "Our Home"
    assert household.is_personal is False
    assert membership is not None
    assert membership.role == "member"


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_household_rejects_invalid_name(session: Session, name: str) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")

    with pytest.raises(WorkspaceRuleError):
        create_household_workspace(session, owner, name)


def test_workspace_listing_and_lookup_use_membership_only(session: Session) -> None:
    alex = create_user(session, "alex-sub", "alex@example.com", "Alex")
    blair = create_user(session, "blair-sub", "blair@example.com", "Blair")
    household = create_household_workspace(session, alex, "Shared Home")
    session.commit()

    alex_workspaces = list_user_workspaces(session, alex.id)
    blair_workspaces = list_user_workspaces(session, blair.id)

    assert {workspace.name for workspace in alex_workspaces} == {"Personal", "Shared Home"}
    assert {workspace.name for workspace in blair_workspaces} == {"Personal"}
    assert get_authorized_workspace(session, alex.id, household.id) == household
    assert get_authorized_workspace(session, blair.id, household.id) is None
    assert get_authorized_workspace(session, blair.id, alex.owned_workspaces[0].id) is None


def test_personal_workspace_cannot_be_invited_into(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")

    with pytest.raises(WorkspaceRuleError):
        create_workspace_invitation(
            session,
            owner.owned_workspaces[0],
            owner,
            "invitee@example.com",
            now=NOW,
        )


def test_outsider_cannot_invite_to_household(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    outsider = create_user(session, "outsider-sub", "outsider@example.com", "Outsider")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()

    with pytest.raises(WorkspaceRuleError):
        create_workspace_invitation(
            session,
            household,
            outsider,
            "invitee@example.com",
            now=NOW,
        )


def test_invitation_normalizes_email_and_stores_only_digest(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()

    dispatch = create_workspace_invitation(
        session,
        household,
        owner,
        "  Invitee@Example.Com  ",
        now=NOW,
    )
    session.commit()

    assert dispatch.invitation.email == "invitee@example.com"
    assert dispatch.invitation.token != dispatch.raw_token
    assert len(dispatch.invitation.token) == 64
    stored_expiry = dispatch.invitation.expires_at.replace(tzinfo=UTC)
    assert stored_expiry == NOW + timedelta(days=7)


def test_invitation_rejects_invalid_email_syntax(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()

    with pytest.raises(InvitationError):
        create_workspace_invitation(
            session,
            household,
            owner,
            "not-an-email",
            now=NOW,
        )


def test_duplicate_live_invitation_is_rejected(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()
    create_workspace_invitation(
        session,
        household,
        owner,
        "invitee@example.com",
        now=NOW,
    )
    session.commit()

    with pytest.raises(InvitationError):
        create_workspace_invitation(
            session,
            household,
            owner,
            "INVITEE@example.com",
            now=NOW + timedelta(hours=1),
        )


def test_current_member_cannot_be_invited_again(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()

    with pytest.raises(InvitationError):
        create_workspace_invitation(
            session,
            household,
            owner,
            "OWNER@example.com",
            now=NOW,
        )


def test_invitation_requires_matching_verified_user_email(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    wrong_user = create_user(session, "wrong-sub", "wrong@example.com", "Wrong")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()
    dispatch = create_workspace_invitation(
        session,
        household,
        owner,
        "invitee@example.com",
        now=NOW,
    )
    session.commit()

    with pytest.raises(InvitationError):
        accept_workspace_invitation(session, wrong_user, dispatch.raw_token, now=NOW)

    assert session.scalar(select(func.count()).select_from(WorkspaceMembership)) == 3


def test_expired_invitation_cannot_be_accepted(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    invitee = create_user(session, "invitee-sub", "invitee@example.com", "Invitee")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()
    dispatch = create_workspace_invitation(
        session,
        household,
        owner,
        invitee.email,
        now=NOW,
    )
    session.commit()

    with pytest.raises(InvitationError):
        accept_workspace_invitation(
            session,
            invitee,
            dispatch.raw_token,
            now=NOW + timedelta(days=8),
        )


def test_invitation_without_expiry_is_unavailable(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    invitee = create_user(session, "invitee-sub", "invitee@example.com", "Invitee")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()
    invitation = WorkspaceInvitation(
        workspace_id=household.id,
        email=invitee.email,
        token=hash_invitation_token("legacy-token"),
        invited_by_id=owner.id,
        accepted=False,
        expires_at=None,
    )
    session.add(invitation)
    session.commit()

    with pytest.raises(InvitationError):
        accept_workspace_invitation(session, invitee, "legacy-token", now=NOW)


def test_acceptance_adds_equal_access_once_and_preserves_private_boundaries(
    session: Session,
) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    invitee = create_user(session, "invitee-sub", "invitee@example.com", "Invitee")
    household = create_household_workspace(session, owner, "Shared Home")
    session.commit()
    dispatch = create_workspace_invitation(
        session,
        household,
        owner,
        invitee.email,
        now=NOW,
    )
    session.commit()

    accepted_workspace = accept_workspace_invitation(
        session,
        invitee,
        dispatch.raw_token,
        now=NOW + timedelta(days=1),
    )
    session.commit()

    membership = session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == household.id,
            WorkspaceMembership.user_id == invitee.id,
        )
    )
    assert accepted_workspace == household
    assert membership is not None
    assert membership.role == "member"
    assert dispatch.invitation.accepted is True
    assert get_authorized_workspace(session, invitee.id, household.id) == household
    assert get_authorized_workspace(session, invitee.id, owner.owned_workspaces[0].id) is None
    assert get_authorized_workspace(session, owner.id, invitee.owned_workspaces[0].id) is None

    with pytest.raises(InvitationError):
        accept_workspace_invitation(session, invitee, dispatch.raw_token, now=NOW)


def test_any_household_member_can_invite(session: Session) -> None:
    owner = create_user(session, "owner-sub", "owner@example.com", "Owner")
    member = create_user(session, "member-sub", "member@example.com", "Member")
    household = create_household_workspace(session, owner, "Shared Home")
    session.add(WorkspaceMembership(workspace_id=household.id, user_id=member.id, role="member"))
    session.commit()

    dispatch = create_workspace_invitation(
        session,
        household,
        member,
        "next@example.com",
        now=NOW,
    )
    session.commit()

    invitation = session.scalar(
        select(WorkspaceInvitation).where(WorkspaceInvitation.id == dispatch.invitation.id)
    )
    assert invitation is not None
    assert invitation.invited_by_id == member.id
