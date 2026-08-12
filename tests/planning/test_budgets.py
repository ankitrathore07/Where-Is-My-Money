from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Budget, Category, Transaction
from app.planning.service import (
    PlanningNotFoundError,
    PlanningValidationError,
    build_budget_month_report,
    parse_money_to_cents,
    save_budget,
)


def _transaction(
    workspace_id: int,
    category_id: int | None,
    occurred_on: date,
    amount_cents: int,
    description: str,
) -> Transaction:
    return Transaction(
        workspace_id=workspace_id,
        category_id=category_id,
        date=datetime.combine(occurred_on, datetime.min.time(), tzinfo=UTC),
        description=description,
        amount_cents=amount_cents,
        categorization_source="manual",
    )


def test_budget_suggestion_uses_prior_three_complete_months_and_half_up_buffer(
    session: Session, workspace
) -> None:
    groceries = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    session.add(groceries)
    session.flush()
    session.add_all(
        (
            _transaction(workspace.id, groceries.id, date(2026, 5, 3), -10_000, "May"),
            _transaction(workspace.id, groceries.id, date(2026, 6, 3), -30_000, "June"),
            _transaction(workspace.id, groceries.id, date(2026, 7, 3), -20_001, "July"),
        )
    )
    session.commit()

    report = build_budget_month_report(session, workspace.id, date(2026, 8, 1))

    assert report.source_start == date(2026, 5, 1)
    assert report.source_end == date(2026, 7, 31)
    assert len(report.lines) == 1
    line = report.lines[0]
    assert line.category_name == "Groceries"
    assert line.suggestion is not None
    assert line.suggestion.monthly_spend_cents == (10_000, 30_000, 20_001)
    assert line.suggestion.median_cents == 20_001
    assert line.suggestion.suggested_cents == 22_001
    assert session.scalar(select(func.count()).select_from(Budget)) == 0


def test_budget_suggestion_counts_zero_month_and_crosses_year_boundary(
    session: Session, workspace
) -> None:
    dining = Category(workspace_id=workspace.id, name="Dining", kind="expense")
    session.add(dining)
    session.flush()
    session.add_all(
        (
            _transaction(workspace.id, dining.id, date(2025, 10, 2), -5_000, "October"),
            _transaction(workspace.id, dining.id, date(2025, 12, 2), -9_000, "December"),
        )
    )
    session.commit()

    report = build_budget_month_report(session, workspace.id, date(2026, 1, 1))

    assert report.source_start == date(2025, 10, 1)
    assert report.source_end == date(2025, 12, 31)
    suggestion = report.lines[0].suggestion
    assert suggestion is not None
    assert suggestion.monthly_spend_cents == (5_000, 0, 9_000)
    assert suggestion.median_cents == 5_000
    assert suggestion.suggested_cents == 5_500


def test_budget_report_excludes_ineligible_and_foreign_spending(
    session: Session, workspace, other_workspace
) -> None:
    expense = Category(workspace_id=workspace.id, name="Travel", kind="expense")
    income = Category(workspace_id=workspace.id, name="Salary", kind="income")
    transfer = Category(workspace_id=workspace.id, name="Transfer", kind="transfer")
    uncategorized = Category(workspace_id=None, name="Uncategorized", kind="expense")
    foreign = Category(workspace_id=other_workspace.id, name="SECRET FOREIGN", kind="expense")
    session.add_all((expense, income, transfer, uncategorized, foreign))
    session.flush()
    session.add_all(
        (
            _transaction(workspace.id, expense.id, date(2026, 7, 1), -2_000, "Travel"),
            _transaction(workspace.id, income.id, date(2026, 7, 1), -50_000, "Income"),
            _transaction(workspace.id, transfer.id, date(2026, 7, 1), -50_000, "Transfer"),
            _transaction(workspace.id, uncategorized.id, date(2026, 7, 1), -50_000, "Unknown"),
            _transaction(workspace.id, None, date(2026, 7, 1), -50_000, "Unassigned"),
            _transaction(other_workspace.id, foreign.id, date(2026, 7, 1), -99_999, "Foreign"),
        )
    )
    session.commit()

    report = build_budget_month_report(session, workspace.id, date(2026, 8, 1))

    assert [(line.category_name, line.suggestion.suggested_cents) for line in report.lines] == [
        ("Travel", 0)
    ]


def test_save_budget_upserts_and_reports_current_spending(session: Session, workspace) -> None:
    groceries = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    session.add(groceries)
    session.flush()
    session.add(_transaction(workspace.id, groceries.id, date(2026, 8, 5), -24_000, "August"))

    created = save_budget(session, workspace.id, groceries.id, date(2026, 8, 1), 22_001)
    session.commit()
    budget_id = created.id
    updated = save_budget(session, workspace.id, groceries.id, date(2026, 8, 1), 21_500)
    session.commit()
    report = build_budget_month_report(session, workspace.id, date(2026, 8, 1))

    assert updated.id == budget_id
    assert updated.amount_cents == 21_500
    assert len(report.lines) == 1
    assert report.lines[0].spent_cents == 24_000
    assert report.lines[0].remaining_cents == -2_500


def test_zero_median_suggestion_can_be_explicitly_accepted(session: Session, workspace) -> None:
    travel = Category(workspace_id=workspace.id, name="Travel", kind="expense")
    session.add(travel)
    session.flush()
    session.add(_transaction(workspace.id, travel.id, date(2026, 7, 5), -5_000, "July"))
    session.commit()

    report = build_budget_month_report(session, workspace.id, date(2026, 8, 1))
    suggestion = report.lines[0].suggestion
    assert suggestion is not None
    assert suggestion.suggested_cents == 0

    budget = save_budget(session, workspace.id, travel.id, date(2026, 8, 1), 0)
    session.commit()

    assert budget.amount_cents == 0
    assert (
        build_budget_month_report(session, workspace.id, date(2026, 8, 1)).lines[0].remaining_cents
        == 0
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    (("220.01", 22_001), ("0.01", 1), ("1", 100), ("1.2", 120), (" 2.50 ", 250)),
)
def test_money_parser_returns_integer_cents(raw: str, expected: int) -> None:
    assert parse_money_to_cents(raw, field="amount") == expected


@pytest.mark.parametrize("raw", ("", "nope", "1.001", "1e2", "0", "-1.00"))
def test_money_parser_rejects_invalid_positive_amounts(raw: str) -> None:
    with pytest.raises(PlanningValidationError) as caught:
        parse_money_to_cents(raw, field="amount")
    assert "amount" in caught.value.field_errors


def test_money_parser_rejects_values_outside_signed_database_integer() -> None:
    with pytest.raises(PlanningValidationError):
        parse_money_to_cents("92233720368547758.08", field="amount")


def test_save_budget_rejects_wrong_month_kind_and_workspace(
    session: Session, workspace, other_workspace
) -> None:
    income = Category(workspace_id=workspace.id, name="Salary", kind="income")
    foreign = Category(workspace_id=other_workspace.id, name="Secret", kind="expense")
    session.add_all((income, foreign))
    session.commit()

    with pytest.raises(PlanningValidationError):
        save_budget(session, workspace.id, income.id, date(2026, 8, 2), 10_000)
    with pytest.raises(PlanningNotFoundError):
        save_budget(session, workspace.id, foreign.id, date(2026, 8, 1), 10_000)
