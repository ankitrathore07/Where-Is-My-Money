"""Bounded, workspace-scoped loading for compiled merchant rules."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Account, Category, MerchantRule
from app.rules.evaluation import (
    CompiledWorkspaceRule,
    CompiledWorkspaceRuleSet,
    RuleCompilationDiagnostic,
)
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
)
from app.rules.validation import RuleConditionValidationError, parse_condition


def load_compiled_rule_set(session: Session, workspace_id: int) -> CompiledWorkspaceRuleSet:
    """Load and validate one workspace's enabled rules with a constant number of queries."""
    rules = tuple(
        session.scalars(
            select(MerchantRule)
            .where(
                MerchantRule.workspace_id == workspace_id,
                MerchantRule.enabled.is_(True),
            )
            .options(selectinload(MerchantRule.tags))
            .order_by(MerchantRule.priority, MerchantRule.id)
        )
    )

    parsed: list[tuple[MerchantRule, ConditionNode]] = []
    diagnostics: list[RuleCompilationDiagnostic] = []
    for rule in rules:
        try:
            condition = parse_condition(_condition_payload(rule))
        except RuleConditionValidationError:
            diagnostics.append(RuleCompilationDiagnostic(rule.id, "invalid_condition"))
            continue
        parsed.append((rule, condition))

    category_ids = {rule.category_id for rule, _condition in parsed if rule.category_id is not None}
    accessible_category_ids = (
        set(
            session.scalars(
                select(Category.id).where(
                    Category.id.in_(category_ids),
                    or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
                )
            )
        )
        if category_ids
        else set()
    )
    referenced_account_ids = {
        account_id
        for _rule, condition in parsed
        for account_id in _referenced_account_ids(condition)
    }
    accessible_account_ids = (
        set(
            session.scalars(
                select(Account.id).where(
                    Account.id.in_(referenced_account_ids),
                    Account.workspace_id == workspace_id,
                )
            )
        )
        if referenced_account_ids
        else set()
    )

    compiled: list[CompiledWorkspaceRule] = []
    for rule, condition in parsed:
        reason = _authorization_failure(
            rule,
            condition,
            workspace_id=workspace_id,
            category_ids=accessible_category_ids,
            account_ids=accessible_account_ids,
        )
        if reason is not None:
            diagnostics.append(RuleCompilationDiagnostic(rule.id, reason))
            continue
        assert rule.category_id is not None
        compiled.append(
            CompiledWorkspaceRule(
                id=rule.id,
                name=rule.name,
                normalized_merchant=rule.normalized_merchant,
                category_id=rule.category_id,
                is_subscription=rule.is_subscription,
                billing_period_months=rule.billing_period_months,
                tag_ids=tuple(tag.id for tag in rule.tags),
                condition=condition,
            )
        )

    order_by_id = {rule.id: index for index, rule in enumerate(rules)}
    diagnostics.sort(key=lambda item: order_by_id[item.rule_id])
    return CompiledWorkspaceRuleSet(workspace_id, tuple(compiled), tuple(diagnostics))


def _condition_payload(rule: MerchantRule) -> object:
    if rule.condition_json == {} and rule.merchant_pattern:
        return {
            "version": 1,
            "type": "predicate",
            "field": "merchant_key",
            "operator": "exact",
            "value": rule.merchant_pattern,
        }
    return rule.condition_json


def _referenced_account_ids(node: ConditionNode) -> tuple[int, ...]:
    if isinstance(node, PredicateCondition):
        if node.field == "account_id" and isinstance(node.value, int):
            return (node.value,)
        return ()
    if isinstance(node, (AllCondition, AnyCondition)):
        return tuple(
            account_id for child in node.children for account_id in _referenced_account_ids(child)
        )
    if isinstance(node, NotCondition):
        return _referenced_account_ids(node.child)
    return ()


def _authorization_failure(
    rule: MerchantRule,
    condition: ConditionNode,
    *,
    workspace_id: int,
    category_ids: set[int],
    account_ids: set[int],
) -> str | None:
    if rule.category_id is None or rule.category_id not in category_ids:
        return "inaccessible_category"
    if any(tag.workspace_id not in {None, workspace_id} for tag in rule.tags):
        return "inaccessible_tag"
    if any(account_id not in account_ids for account_id in _referenced_account_ids(condition)):
        return "inaccessible_account"
    return None
