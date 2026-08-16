import json

import pytest

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
        {"version": 1, "type": "all", "children": []},
        {"version": 1, **predicate("description", "matches", "coffee.*")},
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
