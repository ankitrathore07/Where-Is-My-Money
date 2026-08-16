from dataclasses import replace
from datetime import date

import pytest

from app.rules.evaluation import ConditionResult, evaluate_condition
from app.rules.types import (
    AllCondition,
    AnyCondition,
    NotCondition,
    PredicateCondition,
    RuleContext,
)
from app.rules.validation import parse_condition


def sample_context() -> RuleContext:
    return RuleContext(
        description="  Nétflix   Streaming  ",
        merchant_key="NETFLIX COM",
        amount_cents=-1_549,
        transaction_date=date(2026, 8, 15),
        direction="expense",
        account_id=7,
        provider_key="chase_bank_csv",
    )


def predicate(field: str, operator: str, value: object) -> PredicateCondition:
    return PredicateCondition(field, operator, value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "operator", "value", "expected"),
    [
        ("description", "exact", "nétflix streaming", True),
        ("description", "contains", "NÉTFLIX", True),
        ("description", "starts_with", "nétflix", True),
        ("description", "ends_with", "STREAMING", True),
        ("merchant_key", "exact", "netflix com", True),
        ("merchant_key", "contains", "FLIX", True),
        ("merchant_key", "starts_with", "netflix", True),
        ("merchant_key", "ends_with", "COM", True),
        ("amount_cents", "equal", -1_549, True),
        ("amount_cents", "greater_than", -2_000, True),
        ("amount_cents", "greater_or_equal", -1_549, True),
        ("amount_cents", "less_than", 0, True),
        ("amount_cents", "less_or_equal", -1_549, True),
        ("transaction_date", "on", "2026-08-15", True),
        ("transaction_date", "before", "2026-09-01", True),
        ("transaction_date", "after", "2026-08-01", True),
        ("direction", "equal", "expense", True),
        ("account_id", "equal", 7, True),
        ("provider_key", "equal", "chase_bank_csv", True),
        ("description", "contains", "music", False),
        ("amount_cents", "greater_than", 0, False),
        ("transaction_date", "before", "2026-08-01", False),
        ("direction", "equal", "income", False),
        ("account_id", "equal", 8, False),
        ("provider_key", "equal", "generic_csv", False),
    ],
)
def test_evaluate_condition_handles_each_supported_typed_predicate(
    field: str, operator: str, value: object, expected: bool
) -> None:
    """Break if a supported comparison uses the wrong field-specific semantics."""
    result = evaluate_condition(predicate(field, operator, value), sample_context())

    assert result.matched is expected
    assert result.explanation
    assert result.children == ()


def test_evaluate_condition_normalizes_unicode_text_without_exposing_values() -> None:
    """Break if NFKC/case-insensitive text matching changes or values enter explanations."""
    result = evaluate_condition(
        predicate("description", "exact", " nétflix streaming "), sample_context()
    )

    assert result.matched is True
    assert "Nétflix" not in result.explanation
    assert "streaming" not in result.explanation.casefold()


@pytest.mark.parametrize("field", ["description", "merchant_key"])
def test_evaluate_condition_accepts_task_two_valid_unicode_expanding_text(field: str) -> None:
    """Break if evaluator length checks reject a parsed condition after case-fold expansion."""
    value = "\N{LATIN SMALL LETTER SHARP S}" * 255
    node = parse_condition(
        {
            "version": 1,
            "type": "predicate",
            "field": field,
            "operator": "exact",
            "value": value,
        }
    )
    context = replace(sample_context(), **{field: value})

    result = evaluate_condition(node, context)

    assert result.matched is True


def test_evaluate_condition_short_circuits_all_after_first_non_match() -> None:
    """Break if ALL evaluates children after a false predicate or omits its explanation."""
    result = evaluate_condition(
        AllCondition(
            (
                predicate("amount_cents", "greater_than", 0),
                    predicate("description", "contains", "NÉTFLIX"),
            )
        ),
        sample_context(),
    )

    assert result.matched is False
    assert result.explanation == "all: no match"
    assert len(result.children) == 1
    assert result.children[0].matched is False


def test_evaluate_condition_short_circuits_any_after_first_match() -> None:
    """Break if ANY evaluates children after a true predicate or omits its explanation."""
    result = evaluate_condition(
        AnyCondition(
            (
                predicate("description", "contains", "NÉTFLIX"),
                predicate("amount_cents", "greater_than", 0),
            )
        ),
        sample_context(),
    )

    assert result.matched is True
    assert result.explanation == "any: match"
    assert len(result.children) == 1
    assert result.children[0].matched is True


def test_evaluate_condition_recurses_through_nested_all_any_and_not() -> None:
    """Break if nested group results or NOT inversion are evaluated incorrectly."""
    result = evaluate_condition(
        AllCondition(
            (
                AnyCondition(
                    (
                        predicate("direction", "equal", "income"),
                        predicate("direction", "equal", "expense"),
                    )
                ),
                NotCondition(predicate("provider_key", "equal", "generic_csv")),
            )
        ),
        sample_context(),
    )

    assert result.matched is True
    assert result.explanation == "all: match"
    assert [child.matched for child in result.children] == [True, True]
    assert [child.explanation for child in result.children] == ["any: match", "not: match"]
    assert len(result.children[0].children) == 2
    assert len(result.children[1].children) == 1


@pytest.mark.parametrize(
    "node",
    [
        predicate("description", "matches", "private source value"),
        predicate("amount_cents", "equal", True),
        PredicateCondition("description", ["contains"], "private source value"),  # type: ignore[arg-type]
        AllCondition(()),
        AllCondition(1),  # type: ignore[arg-type]
        object(),
    ],
)
def test_evaluate_condition_fails_closed_for_invalid_direct_nodes(node: object) -> None:
    """Break if bypassed validation can raise or accidentally match a transaction."""
    result = evaluate_condition(node, sample_context())  # type: ignore[arg-type]

    assert result == ConditionResult(matched=False, explanation="invalid condition")


@pytest.mark.parametrize(
    "node",
    [
        NotCondition(
            NotCondition(
                NotCondition(
                    NotCondition(NotCondition(predicate("description", "contains", "NÉTFLIX")))
                )
            )
        ),
        AllCondition(tuple(predicate("description", "contains", "NÉTFLIX") for _ in range(21))),
    ],
)
def test_evaluate_condition_fails_closed_for_trees_beyond_validation_limits(
    node: object,
) -> None:
    """Break if direct AST construction bypasses the validated tree depth or size limits."""
    result = evaluate_condition(node, sample_context())  # type: ignore[arg-type]

    assert result == ConditionResult(matched=False, explanation="invalid condition")
