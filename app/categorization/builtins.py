"""A deliberately small catalog of exact built-in merchant rules."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True)
class BuiltinMerchantRule:
    """Map one exact merchant key to review-friendly display values."""

    merchant_key: str
    normalized_merchant: str
    category_name: str


_BUILTIN_RULES: Final = MappingProxyType(
    {
        "NETFLIX COM": BuiltinMerchantRule("NETFLIX COM", "Netflix", "Entertainment"),
        "SPOTIFY USA": BuiltinMerchantRule("SPOTIFY USA", "Spotify", "Entertainment"),
        "UBER TRIP": BuiltinMerchantRule("UBER TRIP", "Uber", "Transportation"),
    }
)


def find_builtin_rule(merchant_key: str) -> BuiltinMerchantRule | None:
    """Return a rule only when its already-normalized key matches exactly."""
    return _BUILTIN_RULES.get(merchant_key)
