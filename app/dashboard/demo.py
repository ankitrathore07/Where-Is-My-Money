"""Fixed, local-only synthetic data for demonstrating the financial dashboard."""

import argparse
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts.service import create_account
from app.accounts.types import AccountInput
from app.auth.service import normalize_email
from app.db.models import (
    AccountBalanceSnapshot,
    Category,
    Transaction,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.db.session import get_db, init_engine
from app.workspaces.service import create_household_workspace

DEMO_WORKSPACE_NAME = "Dashboard Demo"
DEMO_DATA_PATH = Path(__file__).with_name("demo_data.json")


class DemoAlreadyExistsError(ValueError):
    """Raised when a user can already access the fixed dashboard demo."""


class DemoConfigurationError(RuntimeError):
    """Raised when required built-in categories are unavailable."""


def _fixture() -> dict[str, list[dict[str, object]]]:
    return json.loads(DEMO_DATA_PATH.read_text(encoding="utf-8"))


def _required_categories(
    session: Session, transactions: list[dict[str, object]]
) -> dict[str, Category]:
    names = {str(transaction["category"]) for transaction in transactions}
    categories = {
        category.name: category
        for category in session.scalars(
            select(Category).where(Category.workspace_id.is_(None), Category.name.in_(names))
        )
    }
    missing = sorted(names - categories.keys())
    if missing:
        raise DemoConfigurationError(f"Required built-in category is missing: {', '.join(missing)}")
    return categories


def _user_has_demo_workspace(session: Session, user_id: int) -> bool:
    return (
        session.scalar(
            select(Workspace.id)
            .join(WorkspaceMembership)
            .where(
                Workspace.name == DEMO_WORKSPACE_NAME,
                WorkspaceMembership.user_id == user_id,
            )
            .limit(1)
        )
        is not None
    )


def seed_dashboard_demo(session: Session, user: User) -> Workspace:
    """Stage one fixed fictional dashboard workspace without committing it."""
    fixture = _fixture()
    accounts = fixture["accounts"]
    snapshots = fixture["snapshots"]
    transactions = fixture["transactions"]
    if _user_has_demo_workspace(session, user.id):
        raise DemoAlreadyExistsError("Dashboard Demo already exists for this user.")
    categories = _required_categories(session, transactions)

    with session.begin_nested():
        workspace = create_household_workspace(session, user, DEMO_WORKSPACE_NAME)
        seeded_accounts = {
            str(values["name"]): create_account(
                session,
                workspace.id,
                AccountInput(
                    name=str(values["name"]),
                    account_type=str(values["account_type"]),
                    institution=str(values["institution"]),
                    is_liability=bool(values["is_liability"]),
                ),
            )
            for values in accounts
        }
        session.add_all(
            AccountBalanceSnapshot(
                workspace_id=workspace.id,
                account_id=seeded_accounts[str(values["account"])].id,
                balance_cents=int(values["balance_cents"]),
                as_of_date=date.fromisoformat(str(values["as_of_date"])),
                source="demo",
            )
            for values in snapshots
        )
        session.add_all(
            Transaction(
                workspace_id=workspace.id,
                date=datetime.combine(
                    date.fromisoformat(str(values["date"])), time.min, tzinfo=UTC
                ),
                description=str(values["description"]),
                normalized_merchant=str(values["description"]),
                amount_cents=int(values["amount_cents"]),
                category_id=categories[str(values["category"])].id,
                categorization_source="demo",
            )
            for values in transactions
        )
        session.flush()
    return workspace


def seed_for_email(email: str) -> tuple[int | None, str | None]:
    """Seed the configured database for an already signed-in user email."""
    init_engine()
    db_generator = get_db()
    session = next(db_generator)
    try:
        user = session.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))
        if user is None:
            return None, "No signed-in user found for that email. Sign in once, then try again."
        try:
            workspace = seed_dashboard_demo(session, user)
        except DemoAlreadyExistsError:
            session.rollback()
            return None, "Dashboard Demo already exists for that user; nothing was changed."
        workspace_id = workspace.id
        session.commit()
        return workspace_id, None
    except Exception:
        session.rollback()
        raise
    finally:
        db_generator.close()


def main(argv: list[str] | None = None) -> int:
    """Run the local, opt-in dashboard-demo seeder."""
    parser = argparse.ArgumentParser(description="Seed the fixed Dashboard Demo workspace.")
    parser.add_argument("--email", required=True, help="Email of a user who has signed in before")
    args = parser.parse_args(argv)
    workspace_id, error = seed_for_email(args.email)
    if error is not None:
        print(error)
        return 1
    assert workspace_id is not None
    print(f"Dashboard Demo is ready: /workspaces/{workspace_id}/dashboard?as_of=2026-08-10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
