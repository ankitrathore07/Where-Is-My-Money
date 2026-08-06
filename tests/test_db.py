import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, User, Workspace, WorkspaceInvitation, WorkspaceMembership


@pytest.fixture
def session():
    """Create an in-memory SQLite DB with all tables and yield a session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def test_user_roundtrip(session: Session) -> None:
    """Create and fetch a User record."""
    u = User(google_sub="sub-123", email="test@example.com", display_name="Test")
    session.add(u)
    session.commit()
    assert u.id is not None

    fetched = session.get(User, u.id)
    assert fetched is not None
    assert fetched.email == "test@example.com"


def test_user_email_is_unique(session: Session) -> None:
    """Duplicate emails must be rejected."""
    session.add(User(google_sub="sub-1", email="dup@example.com"))
    session.commit()

    session.add(User(google_sub="sub-2", email="dup@example.com"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_workspace_with_owner(session: Session) -> None:
    """A workspace references its owner via the relationship."""
    owner = User(google_sub="sub-1", email="owner@example.com")
    session.add(owner)
    session.commit()

    ws = Workspace(name="Personal", is_personal=True, owner_id=owner.id)
    session.add(ws)
    session.commit()

    assert ws.id is not None
    assert ws.owner is not None
    assert ws.owner.email == "owner@example.com"


def test_membership_and_unique_constraint(session: Session) -> None:
    """A user can join a workspace, but not twice (unique constraint)."""
    owner = User(google_sub="sub-1", email="owner@example.com")
    member = User(google_sub="sub-2", email="member@example.com")
    session.add_all([owner, member])
    session.commit()

    ws = Workspace(name="Household", is_personal=False, owner_id=owner.id)
    session.add(ws)
    session.commit()

    membership = WorkspaceMembership(workspace_id=ws.id, user_id=member.id, role="member")
    session.add(membership)
    session.commit()
    assert membership.id is not None

    # Duplicate (workspace_id, user_id) must be rejected.
    dup = WorkspaceMembership(workspace_id=ws.id, user_id=member.id, role="member")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_invitation_with_expiry(session: Session) -> None:
    """An invitation stores a token and an optional expiry."""
    from datetime import datetime, timedelta

    owner = User(google_sub="sub-1", email="owner@example.com")
    session.add(owner)
    session.commit()

    ws = Workspace(name="Shared", is_personal=False, owner_id=owner.id)
    session.add(ws)
    session.commit()

    expiry = datetime.now(tz=owner.created_at.tzinfo) + timedelta(days=7)
    invite = WorkspaceInvitation(
        workspace_id=ws.id,
        email="invitee@example.com",
        token="tok-abc",
        invited_by_id=owner.id,
        accepted=False,
        expires_at=expiry,
    )
    session.add(invite)
    session.commit()

    assert invite.id is not None
    assert invite.expires_at is not None
    assert invite.invited_by is not None
