import json
from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.dashboard import demo
from app.dashboard.demo import (
    DEMO_DATA_PATH,
    DemoAlreadyExistsError,
    seed_dashboard_demo,
    seed_for_email,
)
from app.dashboard.service import build_dashboard_report
from app.db.models import Category, Transaction, User, Workspace, WorkspaceMembership


def _seed_builtins(session: Session) -> None:
    session.add_all(
        Category(workspace_id=None, name=name, kind=kind)
        for name, kind in BUILTIN_CATEGORY_DEFINITIONS
    )
    session.flush()


def test_seed_dashboard_demo_creates_the_fixed_report_and_rejects_a_repeat(
    session: Session,
) -> None:
    """Removing repeat protection would overwrite the fixed demo or duplicate its transactions."""
    fixture = json.loads(DEMO_DATA_PATH.read_text(encoding="utf-8"))
    user = User(google_sub="demo-user", email="demo@example.com", display_name="Demo User")
    session.add(user)
    _seed_builtins(session)

    workspace = seed_dashboard_demo(session, user)
    report = build_dashboard_report(session, workspace.id, date(2026, 8, 10))

    assert workspace.name == "Dashboard Demo"
    assert report.position.assets_cents == 36_775_000
    assert report.position.liabilities_cents == 8_313_000
    assert report.position.net_worth_cents == 28_462_000
    assert report.position.cash_cents == 2_484_000
    assert [point.year for point in report.net_worth_series] == [2022, 2023, 2024, 2025, 2026]
    assert [point.year for point in report.cash_flow_series] == [2022, 2023, 2024, 2025, 2026]

    workspace_count = session.scalar(select(func.count()).select_from(Workspace))
    transaction_count = session.scalar(select(func.count()).select_from(Transaction))
    assert transaction_count == len(fixture["transactions"])
    with pytest.raises(DemoAlreadyExistsError):
        seed_dashboard_demo(session, user)

    assert session.scalar(select(func.count()).select_from(Workspace)) == workspace_count
    assert session.scalar(select(func.count()).select_from(Transaction)) == transaction_count


def test_seed_dashboard_demo_uses_only_the_target_users_memberships(session: Session) -> None:
    """Checking global workspace names would prevent an unrelated user from receiving the demo."""
    user = User(google_sub="demo-user", email="demo@example.com", display_name="Demo User")
    other = User(google_sub="other-user", email="other@example.com", display_name="Other User")
    session.add_all((user, other))
    session.flush()
    _seed_builtins(session)
    foreign_demo = Workspace(name="Dashboard Demo", is_personal=False, owner_id=other.id)
    session.add(foreign_demo)
    session.flush()
    session.add(WorkspaceMembership(workspace_id=foreign_demo.id, user_id=other.id, role="member"))
    session.flush()

    workspace = seed_dashboard_demo(session, user)

    assert workspace.id != foreign_demo.id
    assert workspace.owner_id == user.id
    assert (
        session.scalar(
            select(Workspace.id)
            .join(WorkspaceMembership)
            .where(Workspace.id == foreign_demo.id, WorkspaceMembership.user_id == user.id)
        )
        is None
    )


def test_seed_for_email_returns_a_dashboard_url_after_its_session_commits(
    monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """Returning an expired ORM workspace would prevent the CLI from printing its dashboard URL."""
    user = User(
        google_sub="cli-demo-user", email="cli.user@example.com", display_name="CLI Demo User"
    )
    session.add(user)
    _seed_builtins(session)

    def configured_session() -> Generator[Session, None, None]:
        yield session

    monkeypatch.setattr(demo, "init_engine", lambda: None)
    monkeypatch.setattr(demo, "get_db", configured_session)

    workspace_id, error = seed_for_email("CLI.USER@EXAMPLE.COM")

    assert error is None
    assert workspace_id is not None
    assert workspace_id > 0
