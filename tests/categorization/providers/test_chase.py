import pytest

from app.categorization.providers.chase import find_provider_rule


@pytest.mark.parametrize(
    ("description", "amount_cents", "merchant", "category"),
    [
        ("CITI CARD ONLINE PAYMENT 240812 123456789", -10000, "Citi Card Payment", "Transfers"),
        ("CAPITAL ONE MOBILE PMT 240812 123456789", -10000, "Capital One Payment", "Transfers"),
        ("BEST BUY AUTO PYMT 240812 123456789", -2999, "Best Buy Card Payment", "Transfers"),
        ("NEWREZ-SHELLPOINT ACH PMT 240812 123456789", -476114, "Newrez Mortgage", "Housing"),
        ("ZELLE PAYMENT TO JANE SAMPLE 123456789", -5000, "Zelle Transfer", "Transfers"),
        ("ZELLE PAYMENT FROM JOHN SAMPLE 987654321", 5000, "Zelle Transfer", "Transfers"),
    ],
)
def test_confirmed_chase_bank_patterns_are_deterministic(
    description: str,
    amount_cents: int,
    merchant: str,
    category: str,
) -> None:
    rule = find_provider_rule("chase_bank_csv", description, amount_cents)

    assert rule is not None
    assert rule.normalized_merchant == merchant
    assert rule.category_name == category
    assert rule.is_subscription is False


@pytest.mark.parametrize(
    ("description", "amount_cents"),
    [
        ("MICROSOFT EDIPAYMENT 123456789", 500000),
        ("MICROSOFT CTX 123456789", 7500),
        ("XOOM DEBIT 123456789", -1499),
        ("REMOTE ONLINE DEPOSIT 123456789", 46054),
        ("SHOP BEST BUY AUTO PYMT", -2999),
        ("ZELLE PAYMENT TO SAMPLE", 5000),
    ],
)
def test_unconfirmed_partial_or_wrong_direction_patterns_do_not_match(
    description: str, amount_cents: int
) -> None:
    assert find_provider_rule("chase_bank_csv", description, amount_cents) is None


def test_chase_rules_do_not_apply_to_generic_or_credit_card_profiles() -> None:
    description = "BEST BUY AUTO PYMT 240812 123456789"

    assert find_provider_rule(None, description, -2999) is None
    assert find_provider_rule("generic_csv", description, -2999) is None
    assert find_provider_rule("chase_credit_card_csv", description, -2999) is None
