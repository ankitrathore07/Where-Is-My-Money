from datetime import date
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

import app.planning.routes as planning_routes
from app.db.models import SavingsGoal, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in

AS_OF = date(2026, 8, 11)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_travel_goal_calculates_contribution_and_target_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planning_routes, "_utc_today", lambda: AS_OF)
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            deadline_goal = await client.post(
                f"/workspaces/{workspace_id}/planning/goals",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Travel by December",
                    "target_amount": "5000.00",
                    "current_amount": "1000.00",
                    "target_date": "2026-12-31",
                    "monthly_contribution": "",
                },
                follow_redirects=False,
            )
            contribution_goal = await client.post(
                f"/workspaces/{workspace_id}/planning/goals",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Travel with monthly plan",
                    "target_amount": "5000.00",
                    "current_amount": "1000.00",
                    "target_date": "",
                    "monthly_contribution": "800.00",
                },
                follow_redirects=False,
            )
            page = await client.get(f"/workspaces/{workspace_id}/planning?month=2026-08")
            with factory() as session:
                goals = tuple(session.scalars(select(SavingsGoal).order_by(SavingsGoal.id)))
    finally:
        engine.dispose()

    assert deadline_goal.status_code == 303, deadline_goal.text
    assert contribution_goal.status_code == 303, contribution_goal.text
    assert len(goals) == 2
    assert goals[0].target_date == date(2026, 12, 31)
    assert goals[0].monthly_contribution_cents is None
    assert goals[1].target_date is None
    assert goals[1].monthly_contribution_cents == 80_000
    assert page.status_code == 200
    assert "Travel by December" in page.text
    assert "$800.00 per month" in page.text
    assert "Travel with monthly plan" in page.text
    assert page.text.count("December 31, 2026") >= 2
    assert page.text.count("On track") == 2
    assert "Projection" in page.text


@pytest.mark.anyio
async def test_goal_form_validates_exactly_one_plan_and_preserves_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planning_routes, "_utc_today", lambda: AS_OF)
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            missing_csrf = await client.post(
                f"/workspaces/{workspace_id}/planning/goals",
                data={
                    "name": "Travel",
                    "target_amount": "5000.00",
                    "current_amount": "0.00",
                    "target_date": "2026-12-31",
                    "monthly_contribution": "",
                },
            )
            invalid = await client.post(
                f"/workspaces/{workspace_id}/planning/goals",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "  My Travel  ",
                    "target_amount": "5000.001",
                    "current_amount": "100.00",
                    "target_date": "2026-07-31",
                    "monthly_contribution": "800.00",
                },
            )
            with factory() as session:
                goal_count = session.scalar(select(func.count()).select_from(SavingsGoal))
    finally:
        engine.dispose()

    assert missing_csrf.status_code == 403
    assert invalid.status_code == 422
    assert "Enter a dollar amount with at most two decimals." in invalid.text
    assert "Enter exactly one of target date or monthly contribution." in invalid.text
    assert 'value="  My Travel  "' in invalid.text
    assert 'value="800.00"' in invalid.text
    assert goal_count == 0


@pytest.mark.anyio
async def test_blank_goal_fields_return_the_editable_html_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planning_routes, "_utc_today", lambda: AS_OF)
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/planning/goals",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "",
                    "target_amount": "",
                    "current_amount": "",
                    "target_date": "",
                    "monthly_contribution": "",
                },
            )
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "Goal name is required." in response.text
    assert "Enter a dollar amount with at most two decimals." in response.text
    assert "Enter exactly one of target date or monthly contribution." in response.text


@pytest.mark.anyio
async def test_goal_edit_updates_projection_and_hides_foreign_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planning_routes, "_utc_today", lambda: AS_OF)
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                goal = SavingsGoal(
                    workspace_id=workspace_id,
                    name="Travel",
                    target_amount_cents=500_000,
                    current_amount_cents=100_000,
                    target_date=date(2026, 12, 31),
                )
                foreign_user = User(google_sub="goal-foreign", email="goal-foreign@example.com")
                foreign_workspace = Workspace(name="Foreign", is_personal=True, owner=foreign_user)
                session.add_all((goal, foreign_workspace))
                session.flush()
                foreign_goal = SavingsGoal(
                    workspace_id=foreign_workspace.id,
                    name="SECRET FOREIGN GOAL",
                    target_amount_cents=9_999_999,
                    current_amount_cents=0,
                    monthly_contribution_cents=1,
                )
                session.add(foreign_goal)
                session.commit()
                goal_id = goal.id
                foreign_goal_id = foreign_goal.id
            edit = await client.get(f"/workspaces/{workspace_id}/planning/goals/{goal_id}/edit")
            updated = await client.post(
                f"/workspaces/{workspace_id}/planning/goals/{goal_id}",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Travel",
                    "target_amount": "5000.00",
                    "current_amount": "1800.00",
                    "target_date": "2026-12-31",
                    "monthly_contribution": "",
                },
                follow_redirects=False,
            )
            page = await client.get(f"/workspaces/{workspace_id}/planning?month=2026-08")
            foreign_get = await client.get(
                f"/workspaces/{workspace_id}/planning/goals/{foreign_goal_id}/edit"
            )
            foreign_post = await client.post(
                f"/workspaces/{workspace_id}/planning/goals/{foreign_goal_id}",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Changed",
                    "target_amount": "1.00",
                    "current_amount": "0.00",
                    "target_date": "",
                    "monthly_contribution": "1.00",
                },
            )
    finally:
        engine.dispose()

    assert edit.status_code == 200
    assert 'value="Travel"' in edit.text
    assert updated.status_code == 303, updated.text
    assert "$640.00 per month" in page.text
    assert foreign_get.status_code == foreign_post.status_code == 404
    assert "SECRET FOREIGN GOAL" not in foreign_get.text + foreign_post.text
