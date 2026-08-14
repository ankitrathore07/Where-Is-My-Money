"""Confirmed deterministic rules for Chase bank transaction exports."""

from dataclasses import dataclass
from typing import Literal

from app.categorization.sanitization import sanitize_transaction_description

AmountDirection = Literal["expense", "income", "either"]


@dataclass(frozen=True)
class ProviderMerchantRule:
    description: str
    normalized_merchant: str
    category_name: str
    is_subscription: bool = False
    amount_direction: AmountDirection = "expense"


CHASE_BANK_RULES = (
    ProviderMerchantRule(
        "CITI CARD ONLINE PAYMENT", "Citi Card Payment", "Transfers"
    ),
    ProviderMerchantRule(
        "CAPITAL ONE MOBILE PMT", "Capital One Payment", "Transfers"
    ),
    ProviderMerchantRule(
        "BEST BUY AUTO PYMT", "Best Buy Card Payment", "Transfers"
    ),
    ProviderMerchantRule(
        "NEWREZ-SHELLPOINT ACH PMT", "Newrez Mortgage", "Housing"
    ),
    ProviderMerchantRule(
        "ZELLE PAYMENT TO <PAYEE>",
        "Zelle Transfer",
        "Transfers",
        amount_direction="expense",
    ),
    ProviderMerchantRule(
        "ZELLE PAYMENT FROM <PAYER>",
        "Zelle Transfer",
        "Transfers",
        amount_direction="income",
    ),
)


def _direction_matches(rule: ProviderMerchantRule, amount_cents: int) -> bool:
    return (
        rule.amount_direction == "either"
        or (rule.amount_direction == "expense" and amount_cents < 0)
        or (rule.amount_direction == "income" and amount_cents > 0)
    )


def find_provider_rule(
    provider_key: str | None,
    description: str,
    amount_cents: int,
) -> ProviderMerchantRule | None:
    """Return an anchored confirmed rule for one tested provider profile."""
    if provider_key != "chase_bank_csv":
        return None
    sanitized = sanitize_transaction_description(description)
    return next(
        (
            rule
            for rule in CHASE_BANK_RULES
            if rule.description == sanitized and _direction_matches(rule, amount_cents)
        ),
        None,
    )

