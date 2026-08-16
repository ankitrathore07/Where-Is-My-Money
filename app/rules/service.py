"""Workspace-scoped lifecycle management for typed merchant rules."""

from __future__ import annotations

import copy
import json
import unicodedata
from dataclasses import dataclass

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.models import Account, Category, MerchantRule, Tag, Workspace
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
)
from app.rules.validation import RuleConditionValidationError, condition_to_json, parse_condition
from app.tags.service import tag_ids_with_subscription

MAX_RULE_NAME_LENGTH = 120
MAX_MERCHANT_NAME_LENGTH = 255


class RuleNotFoundError(LookupError):
    """Raised when a rule is absent from the active workspace."""


class RuleResourceNotFoundError(LookupError):
    """Raised when a draft references a resource outside the active workspace."""


class RuleConflictError(RuntimeError):
    """Raised when an optimistic-lock version is no longer current."""


class RuleValidationError(ValueError):
    """Raised with field-specific errors for an invalid lifecycle request."""

    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the rule details below.")
        self.field_errors = field_errors


@dataclass(frozen=True)
class RuleDraft:
    """Validated-on-use typed condition and action values for a workspace rule."""

    name: str
    condition: ConditionNode
    normalized_merchant: str | None
    category_id: int
    tag_ids: tuple[int, ...] = ()
    is_subscription: bool = False
    billing_period_months: int | None = None


@dataclass(frozen=True)
class _ValidatedDraft:
    name: str
    condition: ConditionNode
    condition_json: dict[str, object]
    normalized_merchant: str | None
    category_id: int
    tags: tuple[Tag, ...]
    is_subscription: bool
    billing_period_months: int | None


def list_rules(session: Session, workspace_id: int) -> tuple[MerchantRule, ...]:
    """Return one workspace's rules in deterministic execution order."""
    return tuple(
        session.scalars(
            select(MerchantRule)
            .where(MerchantRule.workspace_id == workspace_id)
            .order_by(MerchantRule.priority, MerchantRule.id)
        )
    )


def get_rule(session: Session, workspace_id: int, rule_id: int) -> MerchantRule:
    """Load a rule only through its active workspace boundary."""
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.id == rule_id,
            MerchantRule.workspace_id == workspace_id,
        )
    )
    if rule is None:
        raise RuleNotFoundError("Rule not found.")
    return rule


def create_rule(session: Session, workspace_id: int, draft: RuleDraft) -> MerchantRule:
    """Validate, append, flush, and return a new enabled workspace rule."""
    _serialize_workspace_ordering(session, workspace_id)
    values = _validated_draft(session, workspace_id, draft)
    existing = list(list_rules(session, workspace_id))
    _assign_compact_priorities(existing)
    rule = MerchantRule(
        workspace_id=workspace_id,
        merchant_pattern=None,
        name=values.name,
        enabled=True,
        priority=len(existing),
        condition_version=1,
        condition_json=values.condition_json,
        lock_version=1,
        normalized_merchant=values.normalized_merchant,
        category_id=values.category_id,
        is_subscription=values.is_subscription,
        billing_period_months=values.billing_period_months,
        tags=list(values.tags),
    )
    session.add(rule)
    session.flush()
    return rule


def update_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    draft: RuleDraft,
    *,
    expected_lock_version: int,
) -> MerchantRule:
    """Atomically replace mutable rule values when the submitted version is current."""
    rule = get_rule(session, workspace_id, rule_id)
    _validate_lock_version(expected_lock_version)
    values = _validated_draft(session, workspace_id, draft)
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == rule.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == expected_lock_version,
        )
        .values(
            name=values.name,
            condition_version=1,
            condition_json=values.condition_json,
            lock_version=MerchantRule.lock_version + 1,
            normalized_merchant=values.normalized_merchant,
            category_id=values.category_id,
            is_subscription=values.is_subscription,
            billing_period_months=values.billing_period_months,
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being edited.")
    rule.tags = list(values.tags)
    session.flush()
    return rule


def set_rule_enabled(
    session: Session,
    workspace_id: int,
    rule_id: int,
    enabled: bool,
    *,
    expected_lock_version: int,
) -> MerchantRule:
    """Enable or disable one rule under an optimistic-lock check."""
    rule = get_rule(session, workspace_id, rule_id)
    _validate_lock_version(expected_lock_version)
    if type(enabled) is not bool:
        raise RuleValidationError({"enabled": "Enabled must be a boolean."})
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == rule.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == expected_lock_version,
        )
        .values(enabled=enabled, lock_version=MerchantRule.lock_version + 1)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being edited.")
    session.flush()
    return rule


def move_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    *,
    new_index: int,
    expected_lock_version: int | None = None,
) -> MerchantRule:
    """Move a rule and compact all priorities inside the caller's transaction."""
    _serialize_workspace_ordering(session, workspace_id)
    moved = get_rule(session, workspace_id, rule_id)
    ordered = list(list_rules(session, workspace_id))
    if type(new_index) is not int or not 0 <= new_index < len(ordered):
        raise RuleValidationError({"new_index": "Choose a valid rule position."})
    if expected_lock_version is not None:
        _validate_lock_version(expected_lock_version)
    current_version = moved.lock_version if expected_lock_version is None else expected_lock_version
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == moved.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == current_version,
        )
        .values(lock_version=MerchantRule.lock_version + 1)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being reordered.")
    ordered.remove(moved)
    ordered.insert(new_index, moved)
    _assign_compact_priorities(ordered)
    session.flush()
    return moved


def duplicate_rule(session: Session, workspace_id: int, rule_id: int) -> MerchantRule:
    """Copy a scoped rule and insert the copy immediately after its source."""
    _serialize_workspace_ordering(session, workspace_id)
    source = get_rule(session, workspace_id, rule_id)
    try:
        source_condition = parse_condition(_persisted_condition_payload(source))
    except RuleConditionValidationError:
        raise RuleValidationError({"condition": "Choose a valid rule condition."}) from None
    values = _validated_draft(
        session,
        workspace_id,
        RuleDraft(
            name=_copy_name(source.name),
            condition=source_condition,
            normalized_merchant=source.normalized_merchant,
            category_id=source.category_id,
            tag_ids=tuple(tag.id for tag in source.tags),
            is_subscription=source.is_subscription,
            billing_period_months=source.billing_period_months,
        ),
    )
    ordered = list(list_rules(session, workspace_id))
    source_index = ordered.index(source)
    duplicate = MerchantRule(
        workspace_id=workspace_id,
        merchant_pattern=None,
        name=values.name,
        enabled=source.enabled,
        priority=source_index + 1,
        condition_version=1,
        condition_json=copy.deepcopy(values.condition_json),
        lock_version=1,
        normalized_merchant=values.normalized_merchant,
        category_id=values.category_id,
        is_subscription=values.is_subscription,
        billing_period_months=values.billing_period_months,
        tags=list(values.tags),
    )
    session.add(duplicate)
    session.flush()
    ordered.insert(source_index + 1, duplicate)
    _assign_compact_priorities(ordered)
    session.flush()
    return duplicate


def delete_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    *,
    expected_lock_version: int | None = None,
) -> None:
    """Delete a scoped rule and compact remaining priorities without committing."""
    _serialize_workspace_ordering(session, workspace_id)
    rule = get_rule(session, workspace_id, rule_id)
    if expected_lock_version is not None:
        _validate_lock_version(expected_lock_version)
        result = session.execute(
            update(MerchantRule)
            .where(
                MerchantRule.id == rule.id,
                MerchantRule.workspace_id == workspace_id,
                MerchantRule.lock_version == expected_lock_version,
            )
            .values(lock_version=MerchantRule.lock_version + 1)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise RuleConflictError("The rule changed while it was being deleted.")
    session.delete(rule)
    session.flush()
    _assign_compact_priorities(list(list_rules(session, workspace_id)))
    session.flush()


def _validated_draft(session: Session, workspace_id: int, draft: RuleDraft) -> _ValidatedDraft:
    field_errors: dict[str, str] = {}
    name = _normalized_text(draft.name) if isinstance(draft.name, str) else ""
    if not name:
        field_errors["name"] = "Rule name is required."
    elif len(name) > MAX_RULE_NAME_LENGTH:
        field_errors["name"] = "Rule name must be 120 characters or fewer."

    normalized_merchant: str | None
    if draft.normalized_merchant is None:
        normalized_merchant = None
    elif not isinstance(draft.normalized_merchant, str):
        normalized_merchant = None
        field_errors["normalized_merchant"] = "Merchant name must be text."
    else:
        normalized_merchant = _normalized_text(draft.normalized_merchant) or None
        if normalized_merchant is not None and len(normalized_merchant) > MAX_MERCHANT_NAME_LENGTH:
            field_errors["normalized_merchant"] = "Merchant name must be 255 characters or fewer."

    if type(draft.category_id) is not int or draft.category_id <= 0:
        field_errors["category_id"] = "Choose a valid category."
    if type(draft.is_subscription) is not bool:
        field_errors["is_subscription"] = "Subscription must be a boolean."
    cadence = draft.billing_period_months
    if cadence is not None and (type(cadence) is not int or cadence < 1 or cadence > 120):
        field_errors["billing_period_months"] = "Choose a billing cadence between 1 and 120 months."
    if not isinstance(draft.tag_ids, tuple) or any(
        type(tag_id) is not int or tag_id <= 0 for tag_id in draft.tag_ids
    ):
        field_errors["tag_ids"] = "Choose valid tags."

    condition: ConditionNode | None = None
    condition_json: dict[str, object] | None = None
    try:
        condition_json = json.loads(condition_to_json(draft.condition))
        condition = parse_condition(condition_json)
    except (RuleConditionValidationError, TypeError):
        field_errors["condition"] = "Choose a valid rule condition."

    if field_errors:
        raise RuleValidationError(field_errors)
    assert condition is not None
    assert condition_json is not None

    category = session.scalar(
        select(Category).where(
            Category.id == draft.category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    if category is None:
        raise RuleResourceNotFoundError("Rule resource not found.")

    unique_tag_ids = tuple(dict.fromkeys(draft.tag_ids))
    effective_tag_ids = tag_ids_with_subscription(
        session,
        unique_tag_ids,
        draft.is_subscription,
    )
    tags = _accessible_tags(session, workspace_id, effective_tag_ids)
    _validate_condition_accounts(session, workspace_id, condition)

    return _ValidatedDraft(
        name=name,
        condition=condition,
        condition_json=condition_json,
        normalized_merchant=normalized_merchant,
        category_id=category.id,
        tags=tags,
        is_subscription=draft.is_subscription,
        billing_period_months=cadence,
    )


def _accessible_tags(
    session: Session, workspace_id: int, tag_ids: tuple[int, ...]
) -> tuple[Tag, ...]:
    if not tag_ids:
        return ()
    unique_ids = tuple(dict.fromkeys(tag_ids))
    tags = tuple(
        session.scalars(
            select(Tag)
            .where(
                Tag.id.in_(unique_ids),
                or_(Tag.workspace_id.is_(None), Tag.workspace_id == workspace_id),
            )
            .order_by(Tag.name_key, Tag.id)
        )
    )
    if len(tags) != len(unique_ids):
        raise RuleResourceNotFoundError("Rule resource not found.")
    return tags


def _validate_condition_accounts(
    session: Session, workspace_id: int, condition: ConditionNode
) -> None:
    account_ids = set(_condition_account_ids(condition))
    if not account_ids:
        return
    accessible_ids = set(
        session.scalars(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.workspace_id == workspace_id,
            )
        )
    )
    if accessible_ids != account_ids:
        raise RuleResourceNotFoundError("Rule resource not found.")


def _condition_account_ids(condition: ConditionNode) -> tuple[int, ...]:
    if isinstance(condition, PredicateCondition):
        if condition.field == "account_id" and type(condition.value) is int:
            return (condition.value,)
        return ()
    if isinstance(condition, (AllCondition, AnyCondition)):
        return tuple(
            account_id
            for child in condition.children
            for account_id in _condition_account_ids(child)
        )
    if isinstance(condition, NotCondition):
        return _condition_account_ids(condition.child)
    return ()


def _assign_compact_priorities(rules: list[MerchantRule]) -> None:
    for priority, rule in enumerate(rules):
        rule.priority = priority


def _serialize_workspace_ordering(session: Session, workspace_id: int) -> None:
    """Lock the workspace before reading or rewriting its ordered rule collection."""
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(name=Workspace.name)
            .execution_options(synchronize_session=False)
        )
        found = result.rowcount == 1
    else:
        found = (
            session.scalar(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
            )
            is not None
        )
    if not found:
        raise RuleNotFoundError("Rule not found.")


def _validate_lock_version(version: int) -> None:
    if type(version) is not int or version <= 0:
        raise RuleConflictError("The rule changed while it was being edited.")


def _copy_name(name: str) -> str:
    base = name[: MAX_RULE_NAME_LENGTH - len(" copy")].rstrip()
    return f"{base} copy"


def _persisted_condition_payload(rule: MerchantRule) -> object:
    if rule.condition_json == {} and rule.merchant_pattern:
        return {
            "version": 1,
            "type": "predicate",
            "field": "merchant_key",
            "operator": "exact",
            "value": rule.merchant_pattern,
        }
    return rule.condition_json


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())
