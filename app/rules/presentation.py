"""Human-readable, metric-independent workspace rule summaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from app.categorization.types import CategorizationSource
from app.db.models import Transaction
from app.rules.evaluation import ConditionResult
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
)
from app.rules.validation import RuleConditionValidationError, parse_condition


class RuleActions(Protocol):
    """The action fields shared by drafts, persisted rules, and compiled rules."""

    normalized_merchant: str | None
    category_id: int
    tag_ids: tuple[int, ...]
    is_subscription: bool
    billing_period_months: int | None


@dataclass(frozen=True)
class RuleExplanationLine:
    """One safe, ordered condition-evaluation line for server-rendered simulation."""

    depth: int
    text: str
    matched: bool


@dataclass(frozen=True)
class TransactionExplanation:
    """Truthful, link-ready attribution for a persisted transaction."""

    source_label: str
    detail: str
    rule_id: int | None = None
    rule_name: str | None = None
    condition_summary: str | None = None


_FIELD_LABELS = {
    "description": "description",
    "merchant_key": "merchant",
    "amount_cents": "amount",
    "transaction_date": "date",
    "direction": "direction",
    "account_id": "account",
    "provider_key": "provider",
}
_OPERATOR_LABELS = {
    "exact": "is",
    "contains": "contains",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "equal": "is",
    "greater_than": "is greater than",
    "greater_or_equal": "is at least",
    "less_than": "is less than",
    "less_or_equal": "is at most",
    "on": "is on",
    "before": "is before",
    "after": "is after",
}


def describe_condition(
    condition: ConditionNode,
    *,
    account_names: Mapping[int, str] | None = None,
    provider_names: Mapping[str, str] | None = None,
) -> str:
    """Return a recursive IF summary without flattening boolean semantics."""
    if isinstance(condition, PredicateCondition):
        return _describe_predicate(
            condition,
            account_names=account_names or {},
            provider_names=provider_names or {},
        )
    if isinstance(condition, AllCondition):
        children = "; ".join(
            describe_condition(
                child,
                account_names=account_names,
                provider_names=provider_names,
            )
            for child in condition.children
        )
        return f"all of ({children})"
    if isinstance(condition, AnyCondition):
        children = "; ".join(
            describe_condition(
                child,
                account_names=account_names,
                provider_names=provider_names,
            )
            for child in condition.children
        )
        return f"any of ({children})"
    if isinstance(condition, NotCondition):
        return (
            "not ("
            + describe_condition(
                condition.child,
                account_names=account_names,
                provider_names=provider_names,
            )
            + ")"
        )
    return "invalid condition"


def describe_actions(
    actions: RuleActions,
    *,
    category_name: str | None = None,
    tag_names: Iterable[str] | None = None,
) -> str:
    """Return a complete THEN summary for a typed rule action."""
    parts = []
    if actions.normalized_merchant is None:
        parts.append("derive merchant from transaction description")
    else:
        parts.append(f"set merchant to {_quote(actions.normalized_merchant)}")
    category_label = category_name or f"category {actions.category_id}"
    parts.append(f"set category to {_quote(category_label)}")

    labels = (
        tuple(tag_names)
        if tag_names is not None
        else tuple(f"tag {tag_id}" for tag_id in actions.tag_ids)
    )
    if labels:
        parts.append("replace tags with " + ", ".join(_quote(label) for label in labels))
    else:
        parts.append("clear all tags")
    parts.append(
        "mark as subscription" if actions.is_subscription else "mark as not a subscription"
    )
    if actions.billing_period_months is not None:
        cadence = actions.billing_period_months
        unit = "month" if cadence == 1 else "months"
        parts.append(f"repeat every {cadence} {unit}")
    else:
        parts.append("clear billing cadence")
    return "; ".join(parts)


def describe_evaluation(
    condition: ConditionNode,
    result: ConditionResult,
    *,
    account_names: Mapping[int, str] | None = None,
    provider_names: Mapping[str, str] | None = None,
    depth: int = 0,
) -> tuple[RuleExplanationLine, ...]:
    """Pair a typed condition tree with its safe evaluation result, preserving order."""
    outcome = "match" if result.matched else "no match"
    if isinstance(condition, PredicateCondition):
        summary = describe_condition(
            condition,
            account_names=account_names,
            provider_names=provider_names,
        )
        return (
            RuleExplanationLine(
                depth,
                f"{summary}: {outcome}",
                result.matched,
            ),
        )
    if isinstance(condition, AllCondition):
        label = f"all conditions: {outcome}"
        children = condition.children
    elif isinstance(condition, AnyCondition):
        label = f"any condition: {outcome}"
        children = condition.children
    elif isinstance(condition, NotCondition):
        label = f"not: {outcome}"
        children = (condition.child,)
    else:
        return (RuleExplanationLine(depth, f"invalid condition: {outcome}", False),)
    lines = [RuleExplanationLine(depth, label, result.matched)]
    for child, child_result in zip(children, result.children, strict=False):
        lines.extend(
            describe_evaluation(
                child,
                child_result,
                account_names=account_names,
                provider_names=provider_names,
                depth=depth + 1,
            )
        )
    return tuple(lines)


def transaction_explanation(transaction: Transaction) -> TransactionExplanation:
    """Describe persisted attribution without pretending a deleted rule still exists."""
    try:
        source = CategorizationSource(transaction.categorization_source)
    except ValueError:
        return TransactionExplanation(
            "Unknown categorization source",
            "The stored categorization source is no longer recognized.",
        )
    if source is CategorizationSource.WORKSPACE_RULE:
        rule = transaction.merchant_rule
        if rule is None or rule.workspace_id != transaction.workspace_id:
            return TransactionExplanation(
                "Deleted workspace rule",
                "The workspace rule was deleted; the committed categorization was preserved.",
            )
        payload: object = rule.condition_json
        if payload == {} and rule.merchant_pattern:
            payload = {
                "version": 1,
                "type": "predicate",
                "field": "merchant_key",
                "operator": "exact",
                "value": rule.merchant_pattern,
            }
        try:
            condition_summary = describe_condition(parse_condition(payload))
        except RuleConditionValidationError:
            condition_summary = "Saved condition is no longer available"
        return TransactionExplanation(
            "Workspace rule",
            f"Categorized by workspace rule “{rule.name}”.",
            rule_id=rule.id,
            rule_name=rule.name,
            condition_summary=condition_summary,
        )
    labels = {
        CategorizationSource.MANUAL: (
            "Manual correction",
            "Categorized manually and protected from automatic rule changes.",
        ),
        CategorizationSource.PROVIDER_RULE: (
            "Provider rule",
            "Categorized by a provider-specific rule.",
        ),
        CategorizationSource.BUILTIN_RULE: (
            "Built-in rule",
            "Categorized by a built-in fallback rule.",
        ),
        CategorizationSource.AI_SUGGESTION: (
            "AI suggestion",
            "Categorized from a reviewed AI suggestion.",
        ),
        CategorizationSource.UNCATEGORIZED: (
            "Uncategorized",
            "No committed categorization rule has been attributed.",
        ),
    }
    label, detail = labels[source]
    return TransactionExplanation(label, detail)


def _describe_predicate(
    condition: PredicateCondition,
    *,
    account_names: Mapping[int, str],
    provider_names: Mapping[str, str],
) -> str:
    field = _FIELD_LABELS.get(condition.field, condition.field.replace("_", " "))
    operator = _OPERATOR_LABELS.get(condition.operator, condition.operator.replace("_", " "))
    value: str
    if condition.field == "amount_cents" and type(condition.value) is int:
        value = _format_cents(condition.value)
    elif condition.field == "account_id" and type(condition.value) is int:
        value = _quote(account_names.get(condition.value, f"account {condition.value}"))
    elif condition.field == "provider_key" and isinstance(condition.value, str):
        value = _quote(provider_names.get(condition.value, condition.value))
    elif condition.field in {"description", "merchant_key"} and isinstance(condition.value, str):
        value = _quote(condition.value)
    else:
        value = str(condition.value)
    return f"{field} {operator} {value}"


def _format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def _quote(value: str) -> str:
    return f"\u201c{value}\u201d"
