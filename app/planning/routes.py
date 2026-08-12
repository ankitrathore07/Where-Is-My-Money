"""Authorized server-rendered routes for budgets and savings goals."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import SavingsGoal, User, Workspace
from app.db.session import get_db
from app.planning.presentation import (
    format_date,
    format_money,
    format_money_input,
    format_month,
    format_period,
)
from app.planning.service import (
    GoalNotFoundError,
    PlanningNotFoundError,
    PlanningValidationError,
    build_budget_month_report,
    create_goal,
    get_workspace_goal,
    list_goal_projections,
    parse_money_to_cents,
    save_budget,
    update_goal,
)
from app.planning.types import GoalInput
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}/planning", tags=["planning"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _parse_month(value: str) -> date:
    if len(value) != 7 or value[4] != "-" or not value[:4].isdigit() or not value[5:].isdigit():
        raise ValueError
    return date.fromisoformat(f"{value}-01")


def _context(
    request: Request, user: User, workspace: Workspace, **values: object
) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "csrf_token": request.state.csrf_token,
        "workspace": workspace,
        "format_money": format_money,
        "format_money_input": format_money_input,
        "format_month": format_month,
        "format_date": format_date,
        "format_period": format_period,
        **values,
    }


def _render_planning(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    period_month: date | None,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    field_errors: dict[str, str] | None = None,
    submitted_amount: str = "",
    submitted_category_id: int | None = None,
) -> HTMLResponse:
    report = (
        build_budget_month_report(session, workspace.id, period_month)
        if period_month is not None
        else None
    )
    return templates.TemplateResponse(
        request=request,
        name="planning/index.html",
        context=_context(
            request,
            user,
            workspace,
            period_month=period_month,
            month_value=period_month.strftime("%Y-%m") if period_month else "",
            report=report,
            goals=list_goal_projections(session, workspace.id, _utc_today()),
            error=error,
            field_errors=field_errors or {},
            submitted_amount=submitted_amount,
            submitted_category_id=submitted_category_id,
        ),
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse, name="planning")
async def planning(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    month: str = "",
) -> HTMLResponse:
    """Render one workspace's planning center without creating records."""
    try:
        period_month = _parse_month(month) if month else _utc_today().replace(day=1)
    except ValueError:
        return _render_planning(
            request,
            user,
            session,
            workspace,
            period_month=None,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="Use a valid month in YYYY-MM format.",
        )
    return _render_planning(request, user, session, workspace, period_month=period_month)


@router.post("/budgets", dependencies=[Depends(require_csrf)])
async def budget_save(
    request: Request,
    category_id: Annotated[str, Form()],
    period_month: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    """Persist a category limit only after an explicit member submission."""
    try:
        parsed_month = _parse_month(period_month)
    except ValueError:
        return _render_planning(
            request,
            user,
            session,
            workspace,
            period_month=None,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="Use a valid month in YYYY-MM format.",
            submitted_amount=amount,
        )
    try:
        parsed_category_id = int(category_id)
        if parsed_category_id <= 0:
            raise ValueError
    except ValueError:
        return _render_planning(
            request,
            user,
            session,
            workspace,
            period_month=parsed_month,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="Choose a valid expense category.",
            submitted_amount=amount,
        )
    try:
        cents = parse_money_to_cents(amount, field="amount", allow_zero=True)
        save_budget(session, workspace.id, parsed_category_id, parsed_month, cents)
        session.commit()
    except PlanningNotFoundError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    except PlanningValidationError as exc:
        session.rollback()
        return _render_planning(
            request,
            user,
            session,
            workspace,
            period_month=parsed_month,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error=next(iter(exc.field_errors.values())),
            field_errors=exc.field_errors,
            submitted_amount=amount,
            submitted_category_id=parsed_category_id,
        )
    except IntegrityError:
        session.rollback()
        return _render_planning(
            request,
            user,
            session,
            workspace,
            period_month=parsed_month,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="The budget changed at the same time. Review it and try again.",
            submitted_amount=amount,
            submitted_category_id=parsed_category_id,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/planning?month={period_month}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _empty_goal_values() -> dict[str, str]:
    return {
        "name": "",
        "target_amount": "",
        "current_amount": "0.00",
        "target_date": "",
        "monthly_contribution": "",
    }


def _goal_values(goal: SavingsGoal) -> dict[str, str]:
    return {
        "name": goal.name,
        "target_amount": format_money_input(goal.target_amount_cents),
        "current_amount": format_money_input(goal.current_amount_cents),
        "target_date": goal.target_date.isoformat() if goal.target_date else "",
        "monthly_contribution": (
            format_money_input(goal.monthly_contribution_cents)
            if goal.monthly_contribution_cents is not None
            else ""
        ),
    }


def _render_goal_form(
    request: Request,
    user: User,
    workspace: Workspace,
    *,
    goal: SavingsGoal | None = None,
    values: dict[str, str] | None = None,
    field_errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="planning/goal_form.html",
        context=_context(
            request,
            user,
            workspace,
            goal=goal,
            values=values or (_goal_values(goal) if goal else _empty_goal_values()),
            field_errors=field_errors or {},
            today=_utc_today(),
        ),
        status_code=status_code,
    )


def _parse_goal_date(raw: str) -> date:
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-" or not raw.replace("-", "").isdigit():
        raise ValueError
    return date.fromisoformat(raw)


def _parse_goal_input(values: dict[str, str]) -> GoalInput:
    errors: dict[str, str] = {}
    normalized_name = " ".join(values["name"].split())
    if not normalized_name:
        errors["name"] = "Goal name is required."
    elif len(normalized_name) > 255:
        errors["name"] = "Goal name must be 255 characters or fewer."
    try:
        target_cents = parse_money_to_cents(values["target_amount"], field="target_amount")
    except PlanningValidationError as exc:
        errors.update(exc.field_errors)
        target_cents = 0
    try:
        current_cents = parse_money_to_cents(
            values["current_amount"], field="current_amount", allow_zero=True
        )
    except PlanningValidationError as exc:
        errors.update(exc.field_errors)
        current_cents = 0
    target_date = None
    if values["target_date"].strip():
        try:
            target_date = _parse_goal_date(values["target_date"].strip())
        except ValueError:
            errors["target_date"] = "Use a valid date in YYYY-MM-DD format."
    contribution_cents = None
    if values["monthly_contribution"].strip():
        try:
            contribution_cents = parse_money_to_cents(
                values["monthly_contribution"], field="monthly_contribution"
            )
        except PlanningValidationError as exc:
            errors.update(exc.field_errors)
    if bool(values["target_date"].strip()) == bool(values["monthly_contribution"].strip()):
        message = "Enter exactly one of target date or monthly contribution."
        errors["target_date"] = message
        errors["monthly_contribution"] = message
    if errors:
        raise PlanningValidationError(errors)
    return GoalInput(
        name=values["name"],
        target_amount_cents=target_cents,
        current_amount_cents=current_cents,
        target_date=target_date,
        monthly_contribution_cents=contribution_cents,
    )


def _submitted_goal_values(
    name: str,
    target_amount: str,
    current_amount: str,
    target_date: str,
    monthly_contribution: str,
) -> dict[str, str]:
    return {
        "name": name,
        "target_amount": target_amount,
        "current_amount": current_amount,
        "target_date": target_date,
        "monthly_contribution": monthly_contribution,
    }


@router.get("/goals/new", response_class=HTMLResponse)
async def new_goal(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    del session
    return _render_goal_form(request, user, workspace)


@router.post("/goals", dependencies=[Depends(require_csrf)])
async def goal_create(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    name: Annotated[str, Form()] = "",
    target_amount: Annotated[str, Form()] = "",
    current_amount: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    monthly_contribution: Annotated[str, Form()] = "",
) -> HTMLResponse:
    values = _submitted_goal_values(
        name, target_amount, current_amount, target_date, monthly_contribution
    )
    try:
        goal_input = _parse_goal_input(values)
        create_goal(session, workspace.id, goal_input, _utc_today())
        session.commit()
    except PlanningValidationError as exc:
        session.rollback()
        return _render_goal_form(
            request,
            user,
            workspace,
            values=values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/planning", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/goals/{goal_id}/edit", response_class=HTMLResponse)
async def edit_goal(
    request: Request,
    goal_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        goal = get_workspace_goal(session, workspace.id, goal_id)
    except GoalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    return _render_goal_form(request, user, workspace, goal=goal)


@router.post("/goals/{goal_id}", dependencies=[Depends(require_csrf)])
async def goal_update(
    request: Request,
    goal_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
    name: Annotated[str, Form()] = "",
    target_amount: Annotated[str, Form()] = "",
    current_amount: Annotated[str, Form()] = "",
    target_date: Annotated[str, Form()] = "",
    monthly_contribution: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        goal = get_workspace_goal(session, workspace.id, goal_id)
    except GoalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    values = _submitted_goal_values(
        name, target_amount, current_amount, target_date, monthly_contribution
    )
    try:
        goal_input = _parse_goal_input(values)
        update_goal(session, workspace.id, goal_id, goal_input, _utc_today())
        session.commit()
    except PlanningValidationError as exc:
        session.rollback()
        return _render_goal_form(
            request,
            user,
            workspace,
            goal=goal,
            values=values,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    return RedirectResponse(
        f"/workspaces/{workspace.id}/planning", status_code=status.HTTP_303_SEE_OTHER
    )
