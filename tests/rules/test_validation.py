import json

import pytest

from app.rules.types import AllCondition, NotCondition, PredicateCondition
from app.rules.validation import RuleConditionValidationError, condition_to_json, parse_condition


def predicate(field: str, operator: str, value: object) -> dict[str, object]:
    return {"type": "predicate", "field": field, "operator": operator, "value": value}


def too_deep_tree() -> dict[str, object]:
    return {
        "version": 1,
        "type": "not",
        "child": {
            "type": "not",
            "child": {
                "type": "not",
                "child": {
                    "type": "not",
                    "child": predicate("description", "contains", "coffee"),
                },
            },
        },
    }


def too_many_predicates() -> dict[str, object]:
    return {
        "version": 1,
        "type": "all",
        "children": [predicate("description", "contains", str(index)) for index in range(21)],
    }


def deepest_allowed_tree() -> dict[str, object]:
    return {
        "version": 1,
        "type": "all",
        "children": [
            {
                "type": "any",
                "children": [
                    {
                        "type": "not",
                        "child": predicate("description", "contains", "coffee"),
                    }
                ],
            }
        ],
    }


def test_parse_condition_normalizes_a_typed_group() -> None:
    """Break if text normalization or typed child parsing is removed."""
    node = parse_condition(
        {
            "version": 1,
            "type": "all",
            "children": [
                predicate("description", "contains", "  Cafe\u0301  "),
                {
                    "type": "not",
                    "child": predicate("amount_cents", "less_than", 0),
                },
            ],
        }
    )

    assert json.loads(condition_to_json(node))["children"][0]["value"] == "Café"


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        ("description", "exact", "Netflix"),
        ("description", "contains", "Netflix"),
        ("description", "starts_with", "Net"),
        ("description", "ends_with", "flix"),
        ("merchant_key", "exact", "NETFLIX COM"),
        ("merchant_key", "contains", "NETFLIX"),
        ("merchant_key", "starts_with", "NET"),
        ("merchant_key", "ends_with", "COM"),
        ("amount_cents", "equal", -1_549),
        ("amount_cents", "greater_than", 0),
        ("amount_cents", "greater_or_equal", 0),
        ("amount_cents", "less_than", 0),
        ("amount_cents", "less_or_equal", 0),
        ("transaction_date", "on", "2026-08-15"),
        ("transaction_date", "before", "2026-08-15"),
        ("transaction_date", "after", "2026-08-15"),
        ("direction", "equal", "expense"),
        ("account_id", "equal", 1),
        ("provider_key", "equal", "chase_bank_csv"),
        ("provider_key", "equal", "generic_csv"),
    ],
)
def test_parse_condition_accepts_each_supported_field_operator_and_value(
    field: str, operator: str, value: object
) -> None:
    """Break if a supported typed comparison is rejected or altered."""
    node = parse_condition({"version": 1, **predicate(field, operator, value)})

    assert json.loads(condition_to_json(node)) == {
        "field": field,
        "operator": operator,
        "type": "predicate",
        "value": value,
        "version": 1,
    }


@pytest.mark.parametrize(
    "payload",
    [
        too_deep_tree(),
        too_many_predicates(),
        {"version": 2, **predicate("description", "contains", "coffee")},
        {"version": True, **predicate("description", "contains", "coffee")},
        {"version": 1.0, **predicate("description", "contains", "coffee")},
        {"version": 1, "type": "all", "children": []},
        {"version": 1, "type": [], "children": []},
        {"version": 1, **predicate("description", "matches", "coffee.*")},
        {"version": 1, **predicate("category", "equal", "Dining")},
        {"version": 1, **predicate("amount_cents", "equal", True)},
        {"version": 1, **predicate("account_id", "equal", 0)},
        {"version": 1, **predicate("provider_key", "equal", "unregistered")},
        {"version": 1, **predicate("transaction_date", "on", "15-08-2026")},
        {"version": 1, **predicate("direction", "equal", "outgoing")},
        {
            "version": 1,
            "type": "not",
            "child": predicate("description", "contains", "coffee"),
            "children": [],
        },
    ],
)
def test_parse_condition_rejects_unknown_or_invalid_payloads(payload: object) -> None:
    """Break if a malformed tree or incompatible typed value is accepted."""
    with pytest.raises(RuleConditionValidationError):
        parse_condition(payload)


def test_condition_to_json_is_canonical_and_stable() -> None:
    """Break if equivalent persisted condition JSON varies by key ordering or whitespace."""
    node = parse_condition(
        {
            "version": 1,
            "value": "Coffee",
            "operator": "contains",
            "field": "description",
            "type": "predicate",
        }
    )

    assert condition_to_json(node) == (
        '{"field":"description","operator":"contains","type":"predicate",'
        '"value":"Coffee","version":1}'
    )


def test_parse_condition_accepts_an_any_group() -> None:
    """Break if valid any groups are rejected or serialized as another group type."""
    node = parse_condition(
        {
            "version": 1,
            "type": "any",
            "children": [
                predicate("description", "contains", "coffee"),
                predicate("amount_cents", "less_than", 0),
            ],
        }
    )

    assert json.loads(condition_to_json(node))["type"] == "any"


def test_parse_condition_accepts_the_exact_depth_and_predicate_limits() -> None:
    """Break if the documented maximum depth or predicate count is rejected."""
    depth_limited = parse_condition(deepest_allowed_tree())
    predicate_limited = parse_condition(
        {
            "version": 1,
            "type": "all",
            "children": [predicate("description", "contains", str(index)) for index in range(20)],
        }
    )

    assert json.loads(condition_to_json(depth_limited))["type"] == "all"
    assert len(json.loads(condition_to_json(predicate_limited))["children"]) == 20


@pytest.mark.parametrize(
    "node",
    [
        AllCondition(()),
        PredicateCondition("amount_cents", "equal", True),
        PredicateCondition("description", "matches", "coffee.*"),
        PredicateCondition("description", "contains", "x" * 256),
        NotCondition(
            NotCondition(
                NotCondition(
                    NotCondition(PredicateCondition("description", "contains", "coffee"))
                )
            )
        ),
    ],
)
def test_condition_to_json_rejects_invalid_directly_constructed_nodes(node: object) -> None:
    """Break if direct dataclass construction bypasses the persisted-tree contract."""
    with pytest.raises(RuleConditionValidationError):
        condition_to_json(node)  # type: ignore[arg-type]


def test_condition_to_json_normalizes_a_valid_directly_constructed_node() -> None:
    """Break if serializer validation fails to normalize direct dataclass values."""
    node = PredicateCondition("description", "contains", "  Cafe\u0301  ")

    assert json.loads(condition_to_json(node))["value"] == "Café"
