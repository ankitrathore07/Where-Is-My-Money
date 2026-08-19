from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.db.models import (
    Base,
    MerchantRule,
    RuleApplicationRun,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)


def test_init_engine_enforces_sqlite_foreign_keys_and_audit_actions(tmp_path: Path) -> None:
    """Break if the application SQLite engine leaves audit ownership constraints dormant."""
    previous_engine = db_session.engine
    previous_factory = db_session.SessionLocal
    engine = db_session.init_engine(f"sqlite:///{(tmp_path / 'application.db').as_posix()}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            owner = User(google_sub="fk-owner", email="fk-owner@example.com")
            workspace = Workspace(name="Foreign keys", is_personal=True, owner=owner)
            rule = MerchantRule(workspace=workspace, name="Audit rule")
            session.add_all((workspace, rule))
            session.flush()
            run = RuleApplicationRun(
                workspace=workspace,
                merchant_rule=rule,
                initiated_by_user=owner,
                rule_name_snapshot=rule.name,
                rule_lock_version=1,
                status="previewed",
                selection_json={
                    "normalized_filters": {},
                    "selected_transaction_ids": [],
                },
                preview_digest="a" * 64,
                created_at=datetime.now(UTC),
            )
            session.add(run)
            session.commit()
            workspace_id = workspace.id
            rule_id = rule.id
            run_id = run.id

        with engine.begin() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "insert into rule_application_runs "
                        "(workspace_id, initiated_by_user_id, rule_name_snapshot, "
                        "rule_lock_version, status, selection_json, preview_digest) values "
                        "(999, 999, 'invalid', 1, 'previewed', "
                        '\'{"normalized_filters": {}, "selected_transaction_ids": []}\', '
                        ":digest)"
                    ),
                    {"digest": "b" * 64},
                )

        with engine.begin() as connection:
            connection.execute(
                text("delete from merchant_rules where id = :rule_id"), {"rule_id": rule_id}
            )
            assert (
                connection.scalar(
                    text("select merchant_rule_id from rule_application_runs where id = :run_id"),
                    {"run_id": run_id},
                )
                is None
            )
            connection.execute(
                text("delete from workspaces where id = :workspace_id"),
                {"workspace_id": workspace_id},
            )
            assert (
                connection.scalar(
                    text("select count(*) from rule_application_runs where id = :run_id"),
                    {"run_id": run_id},
                )
                == 0
            )
    finally:
        engine.dispose()
        db_session.engine = previous_engine
        db_session.SessionLocal = previous_factory


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

    dup = WorkspaceMembership(workspace_id=ws.id, user_id=member.id, role="member")
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_invitation_with_expiry(session: Session) -> None:
    """An invitation stores a token and an optional expiry."""
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
