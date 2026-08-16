"""Fail-closed parsing and canonical serialization for workspace rule conditions."""

import json
import unicodedata
from collections.abc import Mapping
from datetime import date
from typing import cast

from app.categorization.normalization import merchant_key
from app.imports.providers.registry import PROVIDER_PDF_PROFILES, PROVIDER_PROFILES
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionField,
    ConditionNode,
    ConditionOperator,
    NotCondition,
    PredicateCondition,
)

CONDITION_VERSION = 1
MAX_TREE_DEPTH = 4
MAX_PREDICATES = 20
MAX_TEXT_LENGTH = 255

_TEXT_FIELDS = frozenset({"description", "merchant_key"})
_TEXT_OPERATORS = frozenset({"exact", "contains", "starts_with", "ends_with"})
_AMOUNT_OPERATORS = frozenset(
    {"equal", "greater_than", "greater_or_equal", "less_than", "less_or_equal"}
)
_DATE_OPERATORS = frozenset({"on", "before", "after"})
_DIRECTIONS = frozenset({"income", "expense", "zero"})
_FIELDS = frozenset(
    {
        "description",
        "merchant_key",
        "amount_cents",
        "transaction_date",
        "direction",
        "account_id",
        "provider_key",
    }
)
_PROVIDER_KEYS = frozenset(
    {"generic_csv", *(profile.key for profile in (*PROVIDER_PROFILES, *PROVIDER_PDF_PROFILES))}
)


class RuleConditionValidationError(ValueError):
    """Raised when a persisted or submitted rule condition is unsupported."""


def parse_condition(payload: object) -> ConditionNode:
    """Return a normalized immutable condition tree, rejecting malformed input."""
    root = _mapping(payload)
    if not _is_int(root.get("version")) or root["version"] != CONDITION_VERSION:
        _invalid("Condition version must be 1.")
    return _parse_node(root, depth=1, predicate_count=[0], root=True)


def condition_to_json(node: ConditionNode) -> str:
    """Serialize a valid typed condition with stable compact JSON formatting."""
    normalized = parse_condition({"version": CONDITION_VERSION, **_node_to_payload(node)})
    payload = {"version": CONDITION_VERSION, **_node_to_payload(normalized)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_node(
    payload: Mapping[str, object], *, depth: int, predicate_count: list[int], root: bool = False
) -> ConditionNode:
    if depth > MAX_TREE_DEPTH:
        _invalid(f"Condition trees may be at most {MAX_TREE_DEPTH} levels deep.")

    expected_keys = {"type"}
    if root:
        expected_keys.add("version")
    node_type = payload.get("type")
    if not isinstance(node_type, str):
        _invalid("Condition node type is not supported.")
    if node_type == "predicate":
        expected_keys.update({"field", "operator", "value"})
        _only_keys(payload, expected_keys)
        predicate_count[0] += 1
        if predicate_count[0] > MAX_PREDICATES:
            _invalid(f"Conditions may contain at most {MAX_PREDICATES} predicates.")
        return _parse_predicate(payload)
    if node_type in {"all", "any"}:
        expected_keys.add("children")
        _only_keys(payload, expected_keys)
        children = payload.get("children")
        if not isinstance(children, list) or not children:
            _invalid("Condition groups must contain at least one child.")
        parsed_children = tuple(
            _parse_node(_mapping(child), depth=depth + 1, predicate_count=predicate_count)
            for child in children
        )
        if node_type == "all":
            return AllCondition(parsed_children)
        return AnyCondition(parsed_children)
    if node_type == "not":
        expected_keys.add("child")
        _only_keys(payload, expected_keys)
        return NotCondition(
            _parse_node(
                _mapping(payload.get("child")), depth=depth + 1, predicate_count=predicate_count
            )
        )
    _invalid("Condition node type is not supported.")


def _parse_predicate(payload: Mapping[str, object]) -> PredicateCondition:
    field = payload.get("field")
    operator = payload.get("operator")
    value = payload.get("value")
    if not isinstance(field, str) or field not in _FIELDS:
        _invalid("Condition field is not supported.")
    if not isinstance(operator, str):
        _invalid("Condition operator is not supported.")

    if field in _TEXT_FIELDS:
        if operator not in _TEXT_OPERATORS:
            _invalid("Text conditions require a text operator.")
        normalized = _normalized_text(value)
        if field == "merchant_key":
            normalized = merchant_key(normalized)
            if not normalized:
                _invalid("Merchant key cannot be empty.")
        return _predicate(field, operator, normalized)
    if field == "amount_cents":
        if operator not in _AMOUNT_OPERATORS or not _is_int(value):
            _invalid("Amount conditions require an integer-cent comparison.")
        return _predicate(field, operator, value)
    if field == "transaction_date":
        if operator not in _DATE_OPERATORS or not _is_iso_date(value):
            _invalid("Date conditions require an ISO date comparison.")
        return _predicate(field, operator, value)
    if field == "direction":
        if operator != "equal" or not isinstance(value, str) or value not in _DIRECTIONS:
            _invalid("Direction conditions require income, expense, or zero.")
        return _predicate(field, operator, value)
    if field == "account_id":
        if operator != "equal" or not _is_int(value) or value <= 0:
            _invalid("Account conditions require a positive account ID.")
        return _predicate(field, operator, value)
    if field == "provider_key":
        if operator != "equal" or not isinstance(value, str) or value not in _PROVIDER_KEYS:
            _invalid("Provider conditions require a registered provider key.")
        return _predicate(field, operator, value)
    _invalid("Condition field is not supported.")


def _node_to_payload(node: ConditionNode) -> dict[str, object]:
    if isinstance(node, PredicateCondition):
        return {
            "type": "predicate",
            "field": node.field,
            "operator": node.operator,
            "value": node.value,
        }
    if isinstance(node, AllCondition):
        return {"type": "all", "children": [_node_to_payload(child) for child in node.children]}
    if isinstance(node, AnyCondition):
        return {"type": "any", "children": [_node_to_payload(child) for child in node.children]}
    if isinstance(node, NotCondition):
        return {"type": "not", "child": _node_to_payload(node.child)}
    _invalid("Condition node type is not supported.")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        _invalid("Condition nodes must be JSON objects.")
    return cast(Mapping[str, object], value)


def _predicate(field: str, operator: str, value: object) -> PredicateCondition:
    return PredicateCondition(
        cast(ConditionField, field),
        cast(ConditionOperator, operator),
        cast(str | int, value),
    )


def _only_keys(payload: Mapping[str, object], expected: set[str]) -> None:
    if set(payload) != expected:
        _invalid("Condition node has unexpected or missing fields.")


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        _invalid("Text conditions require text values.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > MAX_TEXT_LENGTH:
        _invalid(f"Text values must be between 1 and {MAX_TEXT_LENGTH} characters.")
    return normalized


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _invalid(message: str) -> None:
    raise RuleConditionValidationError(message)
