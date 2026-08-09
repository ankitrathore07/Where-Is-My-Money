from collections.abc import Mapping

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.service import (
    GoogleIdentityConflict,
    InvalidGoogleIdentity,
    get_or_create_google_user,
)
from app.db.models import User, Workspace, WorkspaceMembership


def google_claims(**overrides: object) -> Mapping[str, object]:
    claims: dict[str, object] = {
        "sub": "google-sub-alex",
        "email": "Alex@example.test",
        "email_verified": True,
        "name": "Alex Example",
    }
    claims.update(overrides)
    return claims


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": ""},
        {"email": ""},
        {"email_verified": False},
        {"email_verified": "true"},
    ],
)
def test_rejects_incomplete_or_unverified_google_identity(
    session: Session, overrides: dict[str, object]
) -> None:
    with pytest.raises(InvalidGoogleIdentity):
        get_or_create_google_user(session, google_claims(**overrides))


def test_first_login_creates_one_private_workspace_membership(session: Session) -> None:
    user = get_or_create_google_user(session, google_claims())
    session.commit()

    assert user.email == "alex@example.test"
    assert user.display_name == "Alex Example"
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(func.count()).select_from(Workspace)) == 1
    assert session.scalar(select(func.count()).select_from(WorkspaceMembership)) == 1
    personal = user.owned_workspaces[0]
    assert personal.name == "Personal"
    assert personal.is_personal is True
    assert user.memberships[0].workspace_id == personal.id


def test_repeat_login_refreshes_profile_without_duplicate_workspace(session: Session) -> None:
    first = get_or_create_google_user(session, google_claims())
    session.commit()

    repeated = get_or_create_google_user(
        session,
        google_claims(email="updated@example.test", name="Alex Updated"),
    )
    session.commit()

    assert repeated.id == first.id
    assert repeated.email == "updated@example.test"
    assert repeated.display_name == "Alex Updated"
    assert session.scalar(select(func.count()).select_from(User)) == 1
    assert session.scalar(select(func.count()).select_from(Workspace)) == 1
    assert session.scalar(select(func.count()).select_from(WorkspaceMembership)) == 1


def test_existing_pre_auth_user_is_given_private_workspace_membership(session: Session) -> None:
    existing = User(
        google_sub="google-sub-alex",
        email="alex@example.test",
        display_name="Before Auth",
    )
    session.add(existing)
    session.commit()

    user = get_or_create_google_user(session, google_claims())
    session.commit()

    assert user.id == existing.id
    assert session.scalar(select(func.count()).select_from(Workspace)) == 1
    assert session.scalar(select(func.count()).select_from(WorkspaceMembership)) == 1


def test_existing_email_is_never_merged_with_another_google_subject(session: Session) -> None:
    existing = User(
        google_sub="google-sub-existing",
        email="alex@example.test",
        display_name="Existing",
    )
    session.add(existing)
    session.commit()

    with pytest.raises(GoogleIdentityConflict):
        get_or_create_google_user(session, google_claims())

    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_existing_subject_cannot_take_another_users_email(session: Session) -> None:
    alex = get_or_create_google_user(session, google_claims())
    other = User(
        google_sub="google-sub-other",
        email="other@example.test",
        display_name="Other",
    )
    session.add(other)
    session.commit()

    with pytest.raises(GoogleIdentityConflict):
        get_or_create_google_user(
            session,
            google_claims(email="OTHER@example.test"),
        )

    assert alex.email == "alex@example.test"
