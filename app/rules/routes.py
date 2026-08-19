"""Authorized server-rendered management for workspace categorization rules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.accounts.service import list_workspace_accounts
from app.auth.dependencies import require_current_user
from app.categories.service import list_accessible_categories
from app.categorization.normalization import merchant_key
from app.core.middleware import require_csrf
from app.db.models import MerchantRule, Transaction, User, Workspace
from app.db.session import get_db
from app.imports.providers.registry import PROVIDER_PDF_PROFILES, PROVIDER_PROFILES
from app.rules.presentation import describe_actions, describe_condition, describe_evaluation
from app.rules.service import (
    RuleConflictError,
    RuleDraft,
    RuleNotFoundError,
    RuleResourceNotFoundError,
    RuleValidationError,
    create_rule,
    delete_rule,
    duplicate_rule,
    get_rule,
    list_rules,
    move_rule,
    normalize_rule_draft,
    preview_rule_deletion,
    preview_rule_impact,
    set_rule_enabled,
    simulate_rules,
    update_rule,
)
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
    RuleContext,
)
from app.rules.validation import RuleConditionValidationError, condition_to_json, parse_condition
from app.tags.service import list_accessible_tags
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}/rules", tags=["rules"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")

_CONFIRMATION_SALT = "where-is-my-money-rule-save"
_CONFIRMATION_MAX_AGE = 3600
_ROW_PATTERN = re.compile(r"^condition_field_(\d+)$")
_TEXT_FIELDS = frozenset({"description", "merchant_key"})
_TEXT_OPERATORS = ("exact", "contains", "starts_with", "ends_with")
_AMOUNT_OPERATORS = (
    "equal",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
)
_DATE_OPERATORS = ("on", "before", "after")
_PROVIDER_KEYS = tuple(
    dict.fromkeys(
        ["generic_csv", *(item.key for item in (*PROVIDER_PROFILES, *PROVIDER_PDF_PROFILES))]
    )
)


class RuleConfirmationError(ValueError):
    """Raised when an untrusted save confirmation is invalid or expired."""


@dataclass(frozen=True)
class _Confirmation:
    draft: RuleDraft
    rule_id: int | None
    lock_version: int | None


def _context(
    request: Request, user: User, workspace: Workspace, **values: object
) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "csrf_token": request.state.csrf_token,
        "workspace": workspace,
        **values,
    }


def _choices(session: Session, workspace_id: int) -> dict[str, object]:
    categories = list_accessible_categories(session, workspace_id)
    tags = list_accessible_tags(session, workspace_id)
    return {
        "categories": (*categories.workspace, *categories.builtin),
        "tags": (*tags.workspace, *tags.builtin),
        "accounts": list_workspace_accounts(session, workspace_id),
        "providers": tuple((key, key.replace("_", " ").title()) for key in _PROVIDER_KEYS),
        "text_operators": _TEXT_OPERATORS,
        "amount_operators": _AMOUNT_OPERATORS,
        "date_operators": _DATE_OPERATORS,
    }


def _empty_row(index: int | str) -> dict[str, object]:
    return {
        "index": index,
        "field": "description",
        "operator": "contains",
        "negated": False,
        "text_value": "",
        "amount_value": "",
        "date_value": "",
        "direction_value": "expense",
        "account_value": "",
        "provider_value": "",
    }


def _empty_values() -> dict[str, object]:
    return {
        "name": "",
        "group_mode": "all",
        "normalized_merchant": "",
        "category_id": "",
        "tag_ids": (),
        "is_subscription": False,
        "billing_period_months": "",
        "lock_version": "",
    }


def _form_values(form: FormData) -> dict[str, object]:
    return {
        "name": str(form.get("name", "")),
        "group_mode": str(form.get("group_mode", "all")),
        "normalized_merchant": str(form.get("normalized_merchant", "")),
        "category_id": str(form.get("category_id", "")),
        "tag_ids": tuple(str(value) for value in form.getlist("tag_ids")),
        "is_subscription": _submitted_bool(form.get("is_subscription")),
        "billing_period_months": str(form.get("billing_period_months", "")),
        "lock_version": str(form.get("lock_version", "")),
    }


def _submitted_rows(form: FormData) -> list[dict[str, object]]:
    indices = sorted(
        {
            int(match.group(1))
            for key in form
            if (match := _ROW_PATTERN.fullmatch(str(key))) is not None
        }
    )
    rows = []
    for index in indices:
        if _submitted_bool(form.get(f"condition_remove_{index}")):
            continue
        field = str(form.get(f"condition_field_{index}", ""))
        operator = str(form.get(f"condition_operator_{index}", ""))
        rows.append(
            {
                "index": index,
                "field": field,
                "operator": operator,
                "negated": _submitted_bool(form.get(f"condition_negated_{index}")),
                "text_value": str(form.get(f"condition_text_value_{index}", "")),
                "amount_value": str(form.get(f"condition_amount_value_{index}", "")),
                "date_value": str(form.get(f"condition_date_value_{index}", "")),
                "direction_value": str(form.get(f"condition_direction_value_{index}", "")),
                "account_value": str(form.get(f"condition_account_value_{index}", "")),
                "provider_value": str(form.get(f"condition_provider_value_{index}", "")),
            }
        )
    return rows or [_empty_row(0)]


def _submitted_bool(value: object) -> bool:
    return isinstance(value, str) and value.casefold() in {"true", "1", "on", "yes"}


def _parse_positive_int(value: object, field: str, message: str) -> int:
    try:
        parsed = int(str(value))
    except ValueError:
        raise RuleValidationError({field: message}) from None
    if parsed <= 0:
        raise RuleValidationError({field: message})
    return parsed


def _dollars_to_cents(value: str) -> int:
    normalized = value.strip()
    if not normalized or "e" in normalized.casefold():
        raise RuleValidationError({"condition": "Enter an amount with at most two decimals."})
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        raise RuleValidationError({"condition": "Enter a valid condition amount."}) from None
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise RuleValidationError({"condition": "Enter an amount with at most two decimals."})
    return int(amount * 100)


def _row_condition(row: dict[str, object]) -> ConditionNode:
    field = str(row["field"])
    operator = str(row["operator"])
    if field in _TEXT_FIELDS:
        value: str | int = str(row["text_value"])
    elif field == "amount_cents":
        value = _dollars_to_cents(str(row["amount_value"]))
    elif field == "transaction_date":
        value = str(row["date_value"])
    elif field == "direction":
        value = str(row["direction_value"])
    elif field == "account_id":
        value = _parse_positive_int(
            row["account_value"], "condition", "Choose a valid condition account."
        )
    elif field == "provider_key":
        value = str(row["provider_value"])
    else:
        raise RuleValidationError({"condition": "Choose a supported condition field."})
    try:
        condition = parse_condition(
            {
                "version": 1,
                "type": "predicate",
                "field": field,
                "operator": operator,
                "value": value,
            }
        )
    except RuleConditionValidationError as exc:
        raise RuleValidationError({"condition": str(exc)}) from None
    return NotCondition(condition) if bool(row["negated"]) else condition


def _parse_draft(
    form: FormData,
    rows: list[dict[str, object]],
    *,
    condition_override: ConditionNode | None = None,
) -> RuleDraft:
    values = _form_values(form)
    if condition_override is not None:
        condition = condition_override
    else:
        conditions = tuple(_row_condition(row) for row in rows)
        group_mode = str(values["group_mode"])
        if group_mode not in {"all", "any"}:
            raise RuleValidationError({"condition": "Choose whether all or any conditions match."})
        if len(conditions) == 1:
            condition = conditions[0]
        elif group_mode == "all":
            condition = AllCondition(conditions)
        else:
            condition = AnyCondition(conditions)
    category_id = _parse_positive_int(
        values["category_id"], "category_id", "Choose a valid category."
    )
    tag_ids = tuple(
        _parse_positive_int(value, "tag_ids", "Choose valid tags.") for value in values["tag_ids"]
    )
    cadence_text = str(values["billing_period_months"]).strip()
    try:
        cadence = int(cadence_text) if cadence_text else None
    except ValueError:
        raise RuleValidationError(
            {"billing_period_months": "Choose a billing cadence between 1 and 120 months."}
        ) from None
    return RuleDraft(
        name=str(values["name"]),
        condition=condition,
        normalized_merchant=str(values["normalized_merchant"]) or None,
        category_id=category_id,
        tag_ids=tag_ids,
        is_subscription=bool(values["is_subscription"]),
        billing_period_months=cadence,
    )


def _row_from_predicate(
    index: int, predicate: PredicateCondition, *, negated: bool
) -> dict[str, object]:
    row = _empty_row(index)
    row.update({"field": predicate.field, "operator": predicate.operator, "negated": negated})
    if predicate.field in _TEXT_FIELDS:
        row["text_value"] = str(predicate.value)
    elif predicate.field == "amount_cents":
        row["amount_value"] = f"{Decimal(int(predicate.value)) / 100:.2f}"
    elif predicate.field == "transaction_date":
        row["date_value"] = str(predicate.value)
    elif predicate.field == "direction":
        row["direction_value"] = str(predicate.value)
    elif predicate.field == "account_id":
        row["account_value"] = str(predicate.value)
    elif predicate.field == "provider_key":
        row["provider_value"] = str(predicate.value)
    return row


def _builder_rows(condition: ConditionNode) -> tuple[str, list[dict[str, object]]]:
    group_mode = "all"
    children: tuple[ConditionNode, ...]
    if isinstance(condition, AllCondition):
        children = condition.children
    elif isinstance(condition, AnyCondition):
        group_mode = "any"
        children = condition.children
    else:
        children = (condition,)
    rows = []
    for index, child in enumerate(children):
        negated = isinstance(child, NotCondition)
        predicate = child.child if negated else child
        if not isinstance(predicate, PredicateCondition):
            raise RuleValidationError(
                {"condition": "This nested rule cannot be changed with the current visual builder."}
            )
        rows.append(_row_from_predicate(index, predicate, negated=negated))
    return group_mode, rows


def _rule_draft(rule: MerchantRule) -> RuleDraft:
    if rule.category_id is None:
        raise RuleValidationError({"category_id": "Choose a valid category."})
    try:
        condition = parse_condition(rule.condition_json)
    except RuleConditionValidationError as exc:
        raise RuleValidationError({"condition": str(exc)}) from None
    return RuleDraft(
        name=rule.name,
        condition=condition,
        normalized_merchant=rule.normalized_merchant,
        category_id=rule.category_id,
        tag_ids=tuple(tag.id for tag in rule.tags),
        is_subscription=rule.is_subscription,
        billing_period_months=rule.billing_period_months,
    )


def _rule_form_values(rule: MerchantRule, draft: RuleDraft, group_mode: str) -> dict[str, object]:
    return {
        "name": draft.name,
        "group_mode": group_mode,
        "normalized_merchant": draft.normalized_merchant or "",
        "category_id": str(draft.category_id),
        "tag_ids": tuple(str(tag_id) for tag_id in draft.tag_ids),
        "is_subscription": draft.is_subscription,
        "billing_period_months": (
            str(draft.billing_period_months) if draft.billing_period_months is not None else ""
        ),
        "lock_version": str(rule.lock_version),
    }


def _repair_form_values(rule: MerchantRule) -> dict[str, object]:
    """Keep safe saved actions visible while an invalid condition is replaced."""
    return {
        "name": rule.name,
        "group_mode": "all",
        "normalized_merchant": rule.normalized_merchant or "",
        "category_id": str(rule.category_id) if rule.category_id is not None else "",
        "tag_ids": tuple(str(tag.id) for tag in rule.tags),
        "is_subscription": rule.is_subscription,
        "billing_period_months": (
            str(rule.billing_period_months) if rule.billing_period_months is not None else ""
        ),
        "lock_version": str(rule.lock_version),
    }


def _render_form(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    rule: MerchantRule | None = None,
    values: dict[str, object] | None = None,
    rows: list[dict[str, object]] | None = None,
    preserved_condition_summary: str | None = None,
    field_errors: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="rules/form.html",
        context=_context(
            request,
            user,
            workspace,
            rule=rule,
            values=values or _empty_values(),
            rows=rows if rows is not None else [_empty_row(0)],
            preserved_condition_summary=preserved_condition_summary,
            empty_template_row=_empty_row("__INDEX__"),
            field_errors=field_errors or {},
            **_choices(session, workspace.id),
        ),
        status_code=status_code,
    )


def _confirmation_payload(
    workspace_id: int,
    draft: RuleDraft,
    *,
    rule_id: int | None,
    lock_version: int | None,
) -> dict[str, object]:
    return {
        "v": 1,
        "workspace_id": workspace_id,
        "rule_id": rule_id,
        "lock_version": lock_version,
        "draft": {
            "name": draft.name,
            "condition": json.loads(condition_to_json(draft.condition)),
            "normalized_merchant": draft.normalized_merchant,
            "category_id": draft.category_id,
            "tag_ids": list(draft.tag_ids),
            "is_subscription": draft.is_subscription,
            "billing_period_months": draft.billing_period_months,
        },
    }


def _create_confirmation_token(
    request: Request,
    workspace_id: int,
    draft: RuleDraft,
    *,
    rule_id: int | None,
    lock_version: int | None,
) -> str:
    secret_key = request.app.state.settings.secret_key or ""
    serializer = URLSafeTimedSerializer(secret_key, salt=_CONFIRMATION_SALT)
    return serializer.dumps(
        _confirmation_payload(
            workspace_id,
            draft,
            rule_id=rule_id,
            lock_version=lock_version,
        )
    )


def _load_confirmation_token(
    request: Request,
    token: str,
    workspace_id: int,
    *,
    rule_id: int | None,
) -> _Confirmation:
    secret_key = request.app.state.settings.secret_key or ""
    serializer = URLSafeTimedSerializer(secret_key, salt=_CONFIRMATION_SALT)
    try:
        payload = serializer.loads(token, max_age=_CONFIRMATION_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError) as exc:
        raise RuleConfirmationError from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise RuleConfirmationError
    token_rule_id = payload.get("rule_id")
    lock_version = payload.get("lock_version")
    if (
        payload.get("workspace_id") != workspace_id
        or token_rule_id != rule_id
        or (rule_id is None and lock_version is not None)
        or (rule_id is not None and (type(lock_version) is not int or lock_version <= 0))
    ):
        raise RuleConfirmationError
    raw = payload.get("draft")
    if not isinstance(raw, dict):
        raise RuleConfirmationError
    name = raw.get("name")
    merchant = raw.get("normalized_merchant")
    category_id = raw.get("category_id")
    tag_ids = raw.get("tag_ids")
    subscription = raw.get("is_subscription")
    cadence = raw.get("billing_period_months")
    valid = (
        isinstance(name, str)
        and (merchant is None or isinstance(merchant, str))
        and type(category_id) is int
        and category_id > 0
        and isinstance(tag_ids, list)
        and all(type(tag_id) is int and tag_id > 0 for tag_id in tag_ids)
        and len(tag_ids) == len(set(tag_ids))
        and type(subscription) is bool
        and (cadence is None or (type(cadence) is int and 1 <= cadence <= 120))
    )
    if not valid:
        raise RuleConfirmationError
    try:
        condition = parse_condition(raw.get("condition"))
    except RuleConditionValidationError as exc:
        raise RuleConfirmationError from exc
    return _Confirmation(
        RuleDraft(
            name=name,
            condition=condition,
            normalized_merchant=merchant,
            category_id=category_id,
            tag_ids=tuple(tag_ids),
            is_subscription=subscription,
            billing_period_months=cadence,
        ),
        token_rule_id,
        lock_version,
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The rule or preview changed. Reload and try again.",
    )


def _preview_labels(
    session: Session, workspace_id: int, draft: RuleDraft
) -> tuple[str, tuple[str, ...], dict[int, str]]:
    choices = _choices(session, workspace_id)
    categories = {category.id: category.name for category in choices["categories"]}
    tags = {tag.id: tag.name for tag in choices["tags"]}
    accounts = {account.id: account.name for account in choices["accounts"]}
    return (
        categories.get(draft.category_id, "Unavailable category"),
        tuple(tags[tag_id] for tag_id in draft.tag_ids if tag_id in tags),
        accounts,
    )


def _render_preview(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    draft: RuleDraft,
    *,
    rule: MerchantRule | None,
) -> HTMLResponse:
    draft = normalize_rule_draft(session, workspace.id, draft)
    preview = preview_rule_impact(
        session,
        workspace.id,
        draft,
        exclude_rule_id=rule.id if rule else None,
    )
    category_name, tag_names, account_names = _preview_labels(session, workspace.id, draft)
    token = _create_confirmation_token(
        request,
        workspace.id,
        draft,
        rule_id=rule.id if rule else None,
        lock_version=rule.lock_version if rule else None,
    )
    return templates.TemplateResponse(
        request=request,
        name="rules/preview.html",
        context=_context(
            request,
            user,
            workspace,
            rule=rule,
            draft=draft,
            preview=preview,
            deferred_until_enabled=rule is not None and not rule.enabled,
            confirmation_token=token,
            condition_summary=describe_condition(draft.condition, account_names=account_names),
            action_summary=describe_actions(
                draft,
                category_name=category_name,
                tag_names=tag_names,
            ),
        ),
    )


def _rule_views(session: Session, workspace_id: int) -> list[dict[str, object]]:
    rules = list_rules(session, workspace_id)
    categories = list_accessible_categories(session, workspace_id)
    category_names = {
        category.id: category.name for category in (*categories.workspace, *categories.builtin)
    }
    tags = list_accessible_tags(session, workspace_id)
    tag_names = {tag.id: tag.name for tag in (*tags.workspace, *tags.builtin)}
    account_names = {
        account.id: account.name for account in list_workspace_accounts(session, workspace_id)
    }
    usage = {
        rule_id: (count, last_used)
        for rule_id, count, last_used in session.execute(
            select(
                Transaction.merchant_rule_id,
                func.count(Transaction.id),
                func.max(Transaction.created_at),
            )
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.merchant_rule_id.is_not(None),
            )
            .group_by(Transaction.merchant_rule_id)
        )
    }
    views = []
    seen_enabled_conditions: set[str] = set()
    for rule in rules:
        condition_key: str | None = None
        try:
            draft = normalize_rule_draft(session, workspace_id, _rule_draft(rule))
            condition_key = condition_to_json(draft.condition)
            condition_summary = describe_condition(draft.condition, account_names=account_names)
            action_summary = describe_actions(
                draft,
                category_name=category_names.get(draft.category_id),
                tag_names=tuple(tag_names[tag_id] for tag_id in draft.tag_ids),
            )
            repair_error = None
        except (RuleValidationError, RuleResourceNotFoundError):
            condition_summary = "This saved rule needs repair before it can run."
            action_summary = "Action details are unavailable until the rule is repaired."
            repair_error = "Needs repair"
        linked_count, last_used = usage.get(rule.id, (0, None))
        views.append(
            {
                "rule": rule,
                "condition_summary": condition_summary,
                "action_summary": action_summary,
                "linked_count": linked_count,
                "last_used": last_used,
                "conflict_warning": (
                    "Detected identical condition in an earlier enabled rule."
                    if rule.enabled and condition_key in seen_enabled_conditions
                    else None
                ),
                "repair_error": repair_error,
            }
        )
        if rule.enabled and condition_key is not None:
            seen_enabled_conditions.add(condition_key)
    return views


def _simulation_view(session: Session, workspace_id: int, simulation: object) -> dict[str, object]:
    categories = list_accessible_categories(session, workspace_id)
    category_names = {
        category.id: category.name for category in (*categories.workspace, *categories.builtin)
    }
    tags = list_accessible_tags(session, workspace_id)
    tag_names = {tag.id: tag.name for tag in (*tags.workspace, *tags.builtin)}
    account_names = {
        account.id: account.name for account in list_workspace_accounts(session, workspace_id)
    }
    return {
        "result_actions": describe_actions(
            simulation.decision,
            category_name=category_names.get(simulation.decision.category_id),
            tag_names=tuple(
                tag_names[tag_id] for tag_id in simulation.decision.tag_ids if tag_id in tag_names
            ),
        ),
        "matches": tuple(
            {
                "match": match,
                "lines": describe_evaluation(
                    match.rule.condition,
                    match.result,
                    account_names=account_names,
                ),
            }
            for match in simulation.matches
        ),
    }


def _render_index(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    simulation: object | None = None,
    simulator_values: dict[str, str] | None = None,
    simulator_error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="rules/index.html",
        context=_context(
            request,
            user,
            workspace,
            rule_views=_rule_views(session, workspace.id),
            simulation=simulation,
            simulation_view=(
                _simulation_view(session, workspace.id, simulation) if simulation else None
            ),
            simulator_values=simulator_values
            or {
                "description": "",
                "amount": "",
                "transaction_date": date.today().isoformat(),
                "account_id": "",
                "provider_key": "",
            },
            simulator_error=simulator_error,
            **_choices(session, workspace.id),
        ),
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse, name="rules")
async def rule_index(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return _render_index(request, user, session, workspace)


@router.get("/new", response_class=HTMLResponse)
async def rule_new(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return _render_form(request, user, session, workspace)


async def _preview_submission(
    request: Request,
    user: User,
    session: Session,
    workspace: Workspace,
    *,
    rule: MerchantRule | None,
) -> HTMLResponse:
    form = await request.form()
    rows = _submitted_rows(form)
    values = _form_values(form)
    preserved_condition: ConditionNode | None = None
    preserved_condition_summary: str | None = None
    if rule is not None and _submitted_bool(form.get("preserve_condition")):
        preserved_condition = _rule_draft(rule).condition
        account_names = {
            account.id: account.name for account in list_workspace_accounts(session, workspace.id)
        }
        preserved_condition_summary = describe_condition(
            preserved_condition, account_names=account_names
        )
    if form.get("builder_action") == "add_row":
        if len(rows) < 20:
            next_index = max(int(row["index"]) for row in rows) + 1
            rows.append(_empty_row(next_index))
        return _render_form(request, user, session, workspace, rule=rule, values=values, rows=rows)
    try:
        draft = _parse_draft(form, rows, condition_override=preserved_condition)
        return _render_preview(request, user, session, workspace, draft, rule=rule)
    except RuleValidationError as exc:
        session.rollback()
        return _render_form(
            request,
            user,
            session,
            workspace,
            rule=rule,
            values=values,
            rows=rows,
            preserved_condition_summary=preserved_condition_summary,
            field_errors=exc.field_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RuleResourceNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc


@router.post("/preview", dependencies=[Depends(require_csrf)])
async def rule_create_preview(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    return await _preview_submission(request, user, session, workspace, rule=None)


@router.post("", dependencies=[Depends(require_csrf)])
async def rule_create_confirm(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    del user
    form = await request.form()
    try:
        confirmation = _load_confirmation_token(
            request,
            str(form.get("confirmation_token", "")),
            workspace.id,
            rule_id=None,
        )
        create_rule(session, workspace.id, confirmation.draft)
        session.commit()
    except RuleConfirmationError:
        session.rollback()
        raise _conflict() from None
    except RuleResourceNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    except RuleValidationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)


@router.post("/simulate", dependencies=[Depends(require_csrf)])
async def rule_simulate(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    form = await request.form()
    values = {
        key: str(form.get(key, ""))
        for key in (
            "description",
            "amount",
            "transaction_date",
            "account_id",
            "provider_key",
        )
    }
    try:
        amount_cents = _dollars_to_cents(values["amount"])
        transaction_date = date.fromisoformat(values["transaction_date"])
        if transaction_date.isoformat() != values["transaction_date"]:
            raise ValueError
        account_id = (
            _parse_positive_int(values["account_id"], "account_id", "Choose a valid account.")
            if values["account_id"]
            else None
        )
        accessible_account_ids = {
            account.id for account in list_workspace_accounts(session, workspace.id)
        }
        if account_id is not None and account_id not in accessible_account_ids:
            raise RuleResourceNotFoundError
        provider_key = values["provider_key"] or None
        if provider_key is not None and provider_key not in _PROVIDER_KEYS:
            raise RuleValidationError({"provider_key": "Choose a registered provider."})
        direction = "income" if amount_cents > 0 else "expense" if amount_cents < 0 else "zero"
        simulation = simulate_rules(
            session,
            workspace.id,
            RuleContext(
                description=values["description"],
                merchant_key=merchant_key(values["description"]),
                amount_cents=amount_cents,
                transaction_date=transaction_date,
                direction=direction,
                account_id=account_id,
                provider_key=provider_key,
            ),
        )
    except (RuleValidationError, ValueError):
        return _render_index(
            request,
            user,
            session,
            workspace,
            simulator_values=values,
            simulator_error="Enter a valid amount, date, account, and provider.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RuleResourceNotFoundError as exc:
        raise _not_found(exc) from exc
    return _render_index(
        request,
        user,
        session,
        workspace,
        simulation=simulation,
        simulator_values=values,
    )


@router.get("/{rule_id}/edit", response_class=HTMLResponse)
async def rule_edit(
    request: Request,
    rule_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        rule = get_rule(session, workspace.id, rule_id)
        draft = _rule_draft(rule)
        try:
            group_mode, rows = _builder_rows(draft.condition)
            preserved_condition_summary = None
        except RuleValidationError:
            group_mode, rows = "all", []
            account_names = {
                account.id: account.name
                for account in list_workspace_accounts(session, workspace.id)
            }
            preserved_condition_summary = describe_condition(
                draft.condition, account_names=account_names
            )
    except RuleValidationError as exc:
        repair_errors = {
            "repair": "This saved rule needs repair. Replace its condition and review its actions.",
            **exc.field_errors,
        }
        return _render_form(
            request,
            user,
            session,
            workspace,
            rule=rule,
            values=_repair_form_values(rule),
            field_errors=repair_errors,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RuleNotFoundError as exc:
        raise _not_found(exc) from exc
    return _render_form(
        request,
        user,
        session,
        workspace,
        rule=rule,
        values=_rule_form_values(rule, draft, group_mode),
        rows=rows,
        preserved_condition_summary=preserved_condition_summary,
    )


@router.post("/{rule_id}/preview", dependencies=[Depends(require_csrf)])
async def rule_edit_preview(
    request: Request,
    rule_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        rule = get_rule(session, workspace.id, rule_id)
    except RuleNotFoundError as exc:
        raise _not_found(exc) from exc
    form = await request.form()
    try:
        submitted_version = int(str(form.get("lock_version", "")))
    except ValueError:
        submitted_version = 0
    if submitted_version != rule.lock_version:
        raise _conflict()
    return await _preview_submission(request, user, session, workspace, rule=rule)


@router.post("/{rule_id}", dependencies=[Depends(require_csrf)])
async def rule_edit_confirm(
    request: Request,
    rule_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    del user
    form = await request.form()
    try:
        get_rule(session, workspace.id, rule_id)
        confirmation = _load_confirmation_token(
            request,
            str(form.get("confirmation_token", "")),
            workspace.id,
            rule_id=rule_id,
        )
        assert confirmation.lock_version is not None
        update_rule(
            session,
            workspace.id,
            rule_id,
            confirmation.draft,
            expected_lock_version=confirmation.lock_version,
        )
        session.commit()
    except RuleConfirmationError:
        session.rollback()
        raise _conflict() from None
    except RuleConflictError:
        session.rollback()
        raise _conflict() from None
    except (RuleNotFoundError, RuleResourceNotFoundError) as exc:
        session.rollback()
        raise _not_found(exc) from exc
    except RuleValidationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)


@router.post("/{rule_id}/duplicate", dependencies=[Depends(require_csrf)])
async def rule_duplicate(
    rule_id: int,
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    try:
        duplicate_rule(session, workspace.id, rule_id)
        session.commit()
    except RuleNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    except RuleResourceNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    except RuleValidationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)


@router.post("/{rule_id}/move", dependencies=[Depends(require_csrf)])
async def rule_move(
    request: Request,
    rule_id: int,
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    form = await request.form()
    try:
        get_rule(session, workspace.id, rule_id)
        new_index = int(str(form.get("new_index", "")))
        lock_version = int(str(form.get("lock_version", "")))
        move_rule(
            session,
            workspace.id,
            rule_id,
            new_index=new_index,
            expected_lock_version=lock_version,
        )
        session.commit()
    except ValueError:
        session.rollback()
        raise HTTPException(status_code=422, detail="Choose a valid rule position.") from None
    except RuleConflictError:
        session.rollback()
        raise _conflict() from None
    except RuleNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    except RuleValidationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)


@router.post("/{rule_id}/enabled", dependencies=[Depends(require_csrf)])
async def rule_enabled(
    request: Request,
    rule_id: int,
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    form = await request.form()
    try:
        get_rule(session, workspace.id, rule_id)
        lock_version = int(str(form.get("lock_version", "")))
        enabled_text = str(form.get("enabled", ""))
        if enabled_text not in {"true", "false"}:
            raise ValueError
        set_rule_enabled(
            session,
            workspace.id,
            rule_id,
            enabled_text == "true",
            expected_lock_version=lock_version,
        )
        session.commit()
    except ValueError:
        session.rollback()
        raise HTTPException(status_code=422, detail="Choose enabled or disabled.") from None
    except RuleConflictError:
        session.rollback()
        raise _conflict() from None
    except RuleNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)


@router.get("/{rule_id}/delete", response_class=HTMLResponse)
async def rule_delete_review(
    request: Request,
    rule_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    try:
        rule = get_rule(session, workspace.id, rule_id)
        deletion_preview = preview_rule_deletion(session, workspace.id, rule.id)
    except RuleNotFoundError as exc:
        raise _not_found(exc) from exc
    linked_count = session.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.workspace_id == workspace.id,
            Transaction.merchant_rule_id == rule.id,
        )
    )
    return templates.TemplateResponse(
        request=request,
        name="rules/delete.html",
        context=_context(
            request,
            user,
            workspace,
            rule=rule,
            linked_count=linked_count or 0,
            deletion_preview=deletion_preview,
        ),
    )


@router.post("/{rule_id}/delete", dependencies=[Depends(require_csrf)])
async def rule_delete_confirm(
    request: Request,
    rule_id: int,
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> RedirectResponse:
    form = await request.form()
    try:
        get_rule(session, workspace.id, rule_id)
        lock_version = int(str(form.get("lock_version", "")))
        delete_rule(
            session,
            workspace.id,
            rule_id,
            expected_lock_version=lock_version,
        )
        session.commit()
    except ValueError:
        session.rollback()
        raise _conflict() from None
    except RuleConflictError:
        session.rollback()
        raise _conflict() from None
    except RuleNotFoundError as exc:
        session.rollback()
        raise _not_found(exc) from exc
    return RedirectResponse(f"/workspaces/{workspace.id}/rules", status_code=303)
