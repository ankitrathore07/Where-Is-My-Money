from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.dashboard.service import build_cash_flow_series
from app.dashboard.types import AnnualCashFlow
from app.db.models import Category, Transaction, Workspace


def _category(session: Session, name: str, kind: str, workspace_id: int | None = None) -> Category:
    category = Category(workspace_id=workspace_id, name=name, kind=kind)
    session.add(category)
    session.flush()
    return category


def _transaction(
    session: Session,
    workspace_id: int,
    occurred_at: datetime,
    amount_cents: int,
    category_id: int | None,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=occurred_at,
        description="SECRET DESCRIPTION",
        amount_cents=amount_cents,
        category_id=category_id,
        categorization_source="test",
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_cash_flow_classifies_valid_signed_rows_and_reviews_bad_rows(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    income = _category(session, "Salary", "income")
    expense = _category(session, "Housing", "expense", workspace.id)
    transfer = _category(session, "Transfer", "transfer")
    day = datetime(2026, 8, 10, 12, tzinfo=UTC)
    _transaction(session, workspace.id, day, 500_000, income.id)
    _transaction(session, workspace.id, day, -300_000, expense.id)
    _transaction(session, workspace.id, day, 1_000, transfer.id)
    _transaction(session, workspace.id, day, -1_000, transfer.id)
    _transaction(session, workspace.id, day, 2_000, expense.id)
    _transaction(session, workspace.id, day, -2_000, income.id)
    _transaction(session, workspace.id, day, -3_000, None)
    _transaction(session, other_workspace.id, day, 99_999_999, income.id)

    series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=5)

    assert series[-1] == AnnualCashFlow(2026, 500_000, 300_000, 200_000, 4_000, 3)
    assert series[0] == AnnualCashFlow(2022, None, None, None, None, 0)


def test_cash_flow_uses_half_up_basis_point_rounding_and_spending_only_semantics(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    expense = _category(session, "Expense", "expense")
    _transaction(session, workspace.id, datetime(2025, 1, 1, tzinfo=UTC), 8_000, income.id)
    _transaction(session, workspace.id, datetime(2025, 1, 1, tzinfo=UTC), -7_998, expense.id)
    _transaction(session, workspace.id, datetime(2026, 1, 1, tzinfo=UTC), -2_500, expense.id)

    series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=2)

    assert series == (
        AnnualCashFlow(2025, 8_000, 7_998, 2, 3, 0),
        AnnualCashFlow(2026, None, 2_500, -2_500, None, 0),
    )


def test_cash_flow_excludes_rows_outside_the_inclusive_calendar_window(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    _transaction(session, workspace.id, datetime(2021, 12, 31, 23, 59, tzinfo=UTC), 100, income.id)
    _transaction(session, workspace.id, datetime(2026, 8, 10, 23, 59, tzinfo=UTC), 200, income.id)
    _transaction(session, workspace.id, datetime(2026, 8, 11, tzinfo=UTC), 300, income.id)

    series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=5)

    assert series[-1] == AnnualCashFlow(2026, 200, 0, 200, 10_000, 0)


def test_cash_flow_compiles_utc_aware_postgresql_date_bounds(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    _transaction(session, workspace.id, datetime(2026, 8, 10, tzinfo=UTC), 200, income.id)
    statements = []

    def capture_statement(execute_state: object) -> None:
        statements.append(execute_state.statement)  # type: ignore[attr-defined]

    event.listen(session, "do_orm_execute", capture_statement)
    try:
        build_cash_flow_series(session, workspace.id, date(2026, 8, 10))
    finally:
        event.remove(session, "do_orm_execute", capture_statement)

    compiled = statements[0].compile(dialect=postgresql.dialect())
    bounds = [value for value in compiled.params.values() if isinstance(value, datetime)]
    assert bounds == [
        datetime(2022, 1, 1, tzinfo=UTC),
        datetime(2026, 8, 11, tzinfo=UTC),
    ]


def test_cash_flow_assigns_aware_transactions_to_their_utc_calendar_year(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    eastern = timezone(timedelta(hours=-5))
    transaction = _transaction(
        session,
        workspace.id,
        datetime(2025, 12, 31, 19, 30, tzinfo=eastern),
        100,
        income.id,
    )

    series = build_cash_flow_series(session, workspace.id, date(2026, 1, 1), years=2)

    assert transaction.date.astimezone(UTC) == datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    assert series == (
        AnnualCashFlow(2025, None, None, None, None, 0),
        AnnualCashFlow(2026, 100, 0, 100, 10_000, 0),
    )


def test_cash_flow_preserves_the_wall_date_of_database_normalized_naive_timestamps(
    session: Session, workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    transaction = _transaction(
        session,
        workspace.id,
        datetime(2025, 12, 31, 23, 30),
        100,
        income.id,
    )

    series = build_cash_flow_series(session, workspace.id, date(2026, 1, 1), years=2)

    assert transaction.date.tzinfo is None
    assert series == (
        AnnualCashFlow(2025, 100, 0, 100, 10_000, 0),
        AnnualCashFlow(2026, None, None, None, None, 0),
    )


def test_cash_flow_reviews_same_workspace_transaction_with_foreign_custom_category(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    income = _category(session, "Income", "income")
    foreign_income = _category(session, "Foreign income", "income", other_workspace.id)
    day = datetime(2026, 8, 10, tzinfo=UTC)
    _transaction(session, workspace.id, day, 500, income.id)
    _transaction(session, workspace.id, day, 99_999, foreign_income.id)

    series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=1)

    assert series == (AnnualCashFlow(2026, 500, 0, 500, 10_000, 1),)


def test_cash_flow_reviews_same_workspace_transaction_with_unknown_category_kind(
    session: Session, workspace: Workspace
) -> None:
    expense = _category(session, "Expense", "expense")
    unknown = _category(session, "Unknown", "unknown", workspace.id)
    day = datetime(2026, 8, 10, tzinfo=UTC)
    _transaction(session, workspace.id, day, -300, expense.id)
    _transaction(session, workspace.id, day, -99_999, unknown.id)

    series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=1)

    assert series == (AnnualCashFlow(2026, None, 300, -300, None, 1),)
