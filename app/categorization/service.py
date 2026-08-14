"""Workspace-scoped deterministic categorization precedence."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.categorization.builtins import BuiltinMerchantRule, find_builtin_rule
from app.categorization.normalization import merchant_display_fallback, merchant_key
from app.categorization.providers.chase import find_provider_rule
from app.categorization.types import CategorizationDecision, CategorizationSource
from app.db.models import Category, MerchantRule
from app.imports.types import NormalizedTransaction


class CategorizationConfigurationError(RuntimeError):
    """Raised when a required built-in category is missing from the database."""


def _category_name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _accessible_rule_category(
    session: Session, workspace_id: int, category_id: int | None
) -> Category | None:
    if category_id is None:
        return None
    return session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )


def _required_builtin_category(session: Session, name: str) -> Category:
    category = session.scalar(
        select(Category).where(
            Category.workspace_id.is_(None),
            Category.name_key == _category_name_key(name),
        )
    )
    if category is None:
        raise CategorizationConfigurationError(f"Required built-in category is missing: {name}")
    return category


def _direction_matches(rule: BuiltinMerchantRule, amount_cents: int) -> bool:
    return (
        rule.amount_direction == "either"
        or (rule.amount_direction == "expense" and amount_cents < 0)
        or (rule.amount_direction == "income" and amount_cents > 0)
    )


def categorize_candidate(
    session: Session,
    workspace_id: int,
    candidate: NormalizedTransaction,
    *,
    provider_key: str | None = None,
) -> CategorizationDecision:
    """Apply workspace, built-in, then safe-fallback precedence to one PR4 candidate."""
    key = merchant_key(candidate.description)
    workspace_rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.merchant_pattern == key,
        )
    )
    if workspace_rule is not None:
        category = _accessible_rule_category(session, workspace_id, workspace_rule.category_id)
        if category is not None:
            return CategorizationDecision(
                normalized_merchant=workspace_rule.normalized_merchant
                or merchant_display_fallback(candidate.description),
                category_id=category.id,
                is_subscription=workspace_rule.is_subscription,
                source=CategorizationSource.WORKSPACE_RULE,
            )

    provider_rule = find_provider_rule(
        provider_key,
        candidate.description,
        candidate.amount_cents,
    )
    if provider_rule is not None:
        category = _required_builtin_category(session, provider_rule.category_name)
        return CategorizationDecision(
            normalized_merchant=provider_rule.normalized_merchant,
            category_id=category.id,
            is_subscription=provider_rule.is_subscription,
            source=CategorizationSource.PROVIDER_RULE,
        )

    builtin_rule = find_builtin_rule(key)
    if builtin_rule is not None and _direction_matches(builtin_rule, candidate.amount_cents):
        category = _required_builtin_category(session, builtin_rule.category_name)
        return CategorizationDecision(
            normalized_merchant=builtin_rule.normalized_merchant,
            category_id=category.id,
            is_subscription=builtin_rule.is_subscription,
            source=CategorizationSource.BUILTIN_RULE,
        )

    category = _required_builtin_category(session, "Uncategorized")
    return CategorizationDecision(
        normalized_merchant=merchant_display_fallback(candidate.description),
        category_id=category.id,
        is_subscription=False,
        source=CategorizationSource.UNCATEGORIZED,
    )
