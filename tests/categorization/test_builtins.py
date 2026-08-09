import pytest

from app.categorization.builtins import find_builtin_rule
from app.categorization.types import CategorizationDecision, CategorizationSource


@pytest.mark.parametrize(
    ("merchant_key", "merchant", "category"),
    [
        ("NETFLIX COM", "Netflix", "Entertainment"),
        ("SPOTIFY USA", "Spotify", "Entertainment"),
        ("UBER TRIP", "Uber", "Transportation"),
    ],
)
def test_builtin_rule_returns_reviewable_merchant_and_category(
    merchant_key: str, merchant: str, category: str
) -> None:
    rule = find_builtin_rule(merchant_key)

    assert rule is not None
    assert rule.normalized_merchant == merchant
    assert rule.category_name == category


def test_builtin_rule_matching_is_exact() -> None:
    assert find_builtin_rule("NETFLIX COM 1234") is None
    assert find_builtin_rule("netflix com") is None


def test_unknown_merchant_has_no_builtin_rule() -> None:
    assert find_builtin_rule("CORNER STORE") is None


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [
        ("manual", CategorizationSource.MANUAL),
        ("workspace_rule", CategorizationSource.WORKSPACE_RULE),
        ("builtin_rule", CategorizationSource.BUILTIN_RULE),
        ("uncategorized", CategorizationSource.UNCATEGORIZED),
    ],
)
def test_categorization_source_parses_persisted_values(
    stored_value: str, expected: CategorizationSource
) -> None:
    assert CategorizationSource(stored_value) is expected


def test_categorization_decision_keeps_source_as_a_string_enum() -> None:
    decision = CategorizationDecision(
        normalized_merchant="Netflix",
        category_id=42,
        source=CategorizationSource.BUILTIN_RULE,
    )

    assert decision.source.value == "builtin_rule"
