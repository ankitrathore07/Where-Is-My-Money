from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.db.models import SavingsGoal
from app.planning.service import (
    GoalNotFoundError,
    PlanningValidationError,
    create_goal,
    get_workspace_goal,
    list_goal_projections,
    project_goal,
    update_goal,
)
from app.planning.types import GoalInput

AS_OF = date(2026, 8, 11)


def _goal(workspace_id: int, **overrides) -> SavingsGoal:
    values = {
        "workspace_id": workspace_id,
        "name": "Travel",
        "target_amount_cents": 500_000,
        "current_amount_cents": 100_000,
        "target_date": date(2026, 12, 31),
        "monthly_contribution_cents": None,
    }
    values.update(overrides)
    return SavingsGoal(**values)


def test_deadline_goal_calculates_required_travel_contribution(workspace) -> None:
    projection = project_goal(_goal(workspace.id), AS_OF)

    assert projection.remaining_cents == 400_000
    assert projection.contribution_months == 5
    assert projection.calculated_monthly_contribution_cents == 80_000
    assert projection.calculated_target_date is None
    assert projection.status == "on_track"


def test_contribution_goal_calculates_travel_target_date(workspace) -> None:
    goal = _goal(
        workspace.id,
        target_date=None,
        monthly_contribution_cents=80_000,
    )

    projection = project_goal(goal, AS_OF)

    assert projection.contribution_months == 5
    assert projection.calculated_target_date == date(2026, 12, 31)
    assert projection.calculated_monthly_contribution_cents is None
    assert projection.status == "on_track"


def test_goal_projection_ceilings_cross_year_and_handles_same_month(workspace) -> None:
    contribution_projection = project_goal(
        _goal(
            workspace.id,
            target_amount_cents=100_001,
            current_amount_cents=0,
            target_date=date(2026, 8, 31),
        ),
        AS_OF,
    )
    date_projection = project_goal(
        _goal(
            workspace.id,
            target_amount_cents=500_001,
            current_amount_cents=0,
            target_date=None,
            monthly_contribution_cents=100_000,
        ),
        AS_OF,
    )

    assert contribution_projection.contribution_months == 1
    assert contribution_projection.calculated_monthly_contribution_cents == 100_001
    assert date_projection.contribution_months == 6
    assert date_projection.calculated_target_date == date(2027, 1, 31)


def test_completed_and_overdue_goal_statuses(workspace) -> None:
    completed = project_goal(
        _goal(
            workspace.id,
            current_amount_cents=550_000,
            target_date=None,
            monthly_contribution_cents=10_000,
        ),
        AS_OF,
    )
    overdue = project_goal(
        _goal(workspace.id, target_date=date(2026, 7, 31)),
        AS_OF,
    )

    assert completed.remaining_cents == 0
    assert completed.status == "completed"
    assert completed.contribution_months == 0
    assert completed.calculated_target_date == AS_OF
    assert overdue.status == "off_track"
    assert overdue.contribution_months is None
    assert overdue.calculated_monthly_contribution_cents is None


def test_goal_create_and_update_preserve_one_supplied_input(session: Session, workspace) -> None:
    created = create_goal(
        session,
        workspace.id,
        GoalInput("  Summer   Travel ", 500_000, 100_000, date(2026, 12, 31), None),
        AS_OF,
    )
    session.commit()
    goal_id = created.id

    updated = update_goal(
        session,
        workspace.id,
        goal_id,
        GoalInput("Summer Travel", 500_000, 180_000, None, 80_000),
        AS_OF,
    )
    session.commit()

    assert updated.id == goal_id
    assert updated.name == "Summer Travel"
    assert updated.current_amount_cents == 180_000
    assert updated.target_date is None
    assert updated.monthly_contribution_cents == 80_000
    assert list_goal_projections(session, workspace.id, AS_OF) == (project_goal(updated, AS_OF),)


@pytest.mark.parametrize(
    "goal_input",
    (
        GoalInput("", 100, 0, date(2026, 12, 31), None),
        GoalInput("x" * 256, 100, 0, date(2026, 12, 31), None),
        GoalInput("Goal", 0, 0, date(2026, 12, 31), None),
        GoalInput("Goal", 100, -1, date(2026, 12, 31), None),
        GoalInput("Goal", 100, 0, None, None),
        GoalInput("Goal", 100, 0, date(2026, 12, 31), 10),
        GoalInput("Goal", 100, 0, None, 0),
        GoalInput("Goal", 100, 0, date(2026, 7, 31), None),
        GoalInput("Goal", 2**63, 0, date(2026, 12, 31), None),
    ),
)
def test_create_goal_rejects_invalid_plans(
    session: Session, workspace, goal_input: GoalInput
) -> None:
    with pytest.raises(PlanningValidationError):
        create_goal(session, workspace.id, goal_input, AS_OF)


def test_goal_lookup_and_update_are_workspace_scoped(
    session: Session, workspace, other_workspace
) -> None:
    foreign = _goal(other_workspace.id, name="SECRET TRAVEL", target_amount_cents=9_999_999)
    session.add(foreign)
    session.commit()

    with pytest.raises(GoalNotFoundError):
        get_workspace_goal(session, workspace.id, foreign.id)
    with pytest.raises(GoalNotFoundError):
        update_goal(
            session,
            workspace.id,
            foreign.id,
            GoalInput("Changed", 100, 0, None, 10),
            AS_OF,
        )

    session.refresh(foreign)
    assert foreign.name == "SECRET TRAVEL"
    assert foreign.target_amount_cents == 9_999_999
