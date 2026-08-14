"""Stable built-in tags used by categorization and analytics."""

from typing import Final

BUILTIN_TAG_NAMES: Final = (
    "Subscription",
    "Household Expenditure",
    "Vehicle",
    "Essential",
    "Family Support",
    "Insurance",
    "Tax Refund",
    "Installment Plan",
)

BUILTIN_TAG_KEYS: Final = {" ".join(name.split()).casefold(): name for name in BUILTIN_TAG_NAMES}
