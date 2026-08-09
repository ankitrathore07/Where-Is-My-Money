"""Small values shared across categorization boundaries."""

from dataclasses import dataclass
from enum import StrEnum


class CategorizationSource(StrEnum):
    """Stable values stored with each transaction decision."""

    MANUAL = "manual"
    WORKSPACE_RULE = "workspace_rule"
    BUILTIN_RULE = "builtin_rule"
    UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True)
class CategorizationDecision:
    """Merchant and category fields approved during transaction review."""

    normalized_merchant: str
    category_id: int
    is_subscription: bool
    source: CategorizationSource
