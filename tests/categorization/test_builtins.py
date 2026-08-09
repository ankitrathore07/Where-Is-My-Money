import pytest

from app.categorization.builtins import (
    BUILTIN_CATEGORY_DEFINITIONS,
    BUILTIN_MERCHANT_RULES,
    BuiltinMerchantRule,
    _build_rule_lookup,
    find_builtin_rule,
)
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


def test_catalog_has_complete_unique_v1_coverage() -> None:
    keys = [key for rule in BUILTIN_MERCHANT_RULES for key in rule.merchant_keys]
    subscriptions = [
        key for rule in BUILTIN_MERCHANT_RULES if rule.is_subscription for key in rule.merchant_keys
    ]

    assert len(BUILTIN_CATEGORY_DEFINITIONS) == 21
    assert len(keys) == 106
    assert len(set(keys)) == 106
    assert len(subscriptions) == 30


def test_builtin_category_definitions_are_unique() -> None:
    names = [name for name, _ in BUILTIN_CATEGORY_DEFINITIONS]

    assert len(names) == len(set(names))
    assert set(BUILTIN_CATEGORY_DEFINITIONS) >= {
        ("Dining & Drinks", "expense"),
        ("Software & Online Services", "expense"),
        ("Income", "income"),
        ("Transfers", "transfer"),
        ("Uncategorized", "expense"),
    }


@pytest.mark.parametrize(
    "merchant_key",
    ["PAYPAL", "VENMO", "ZELLE", "CASH APP", "APPLE COM BILL", "GOOGLE", "SQUARE", "STRIPE"],
)
def test_ambiguous_processors_have_no_builtin_rule(merchant_key: str) -> None:
    assert find_builtin_rule(merchant_key) is None


@pytest.mark.parametrize(
    ("merchant_key", "category", "is_subscription", "direction"),
    [
        ("PAYROLL", "Income", False, "income"),
        ("NETFLIX COM", "Entertainment", True, "expense"),
        ("DOORDASH", "Dining & Drinks", False, "expense"),
        ("DOORDASH DASHPASS", "Dining & Drinks", True, "expense"),
        ("INTERNAL TRANSFER", "Transfers", False, "either"),
    ],
)
def test_rules_carry_category_subscription_and_direction(
    merchant_key: str, category: str, is_subscription: bool, direction: str
) -> None:
    rule = find_builtin_rule(merchant_key)

    assert rule is not None
    assert rule.category_name == category
    assert rule.is_subscription is is_subscription
    assert rule.amount_direction == direction


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
        is_subscription=True,
        source=CategorizationSource.BUILTIN_RULE,
    )

    assert decision.source.value == "builtin_rule"
    assert decision.is_subscription is True


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (BuiltinMerchantRule(("",), "Blank", "Shopping"), "blank"),
        (BuiltinMerchantRule(("not canonical",), "Case", "Shopping"), "canonical"),
        (BuiltinMerchantRule(("UNLISTED",), "Unknown", "Missing"), "category"),
        (
            BuiltinMerchantRule(
                ("BAD DIRECTION",), "Direction", "Shopping", amount_direction="sideways"
            ),  # type: ignore[arg-type]
            "direction",
        ),
        (
            BuiltinMerchantRule(
                ("BAD SUBSCRIPTION",), "Subscription", "Shopping", is_subscription=1
            ),  # type: ignore[arg-type]
            "subscription",
        ),
    ],
)
def test_catalog_validation_rejects_malformed_rules(
    rule: BuiltinMerchantRule, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _build_rule_lookup((rule,))


def test_catalog_validation_rejects_duplicate_keys() -> None:
    duplicate_rules = (
        BuiltinMerchantRule(("DUPLICATE",), "First", "Shopping"),
        BuiltinMerchantRule(("DUPLICATE",), "Second", "Shopping"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        _build_rule_lookup(duplicate_rules)
