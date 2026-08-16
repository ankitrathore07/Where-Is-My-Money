"""Human-readable, metric-independent workspace rule summaries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
)


class RuleActions(Protocol):
    """The action fields shared by drafts, persisted rules, and compiled rules."""

    normalized_merchant: str | None
    category_id: int
    tag_ids: tuple[int, ...]
    is_subscription: bool
    billing_period_months: int | None


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
