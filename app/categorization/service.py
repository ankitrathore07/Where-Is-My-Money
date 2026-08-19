"""Workspace-scoped deterministic categorization precedence."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categorization.builtins import BuiltinMerchantRule, find_builtin_rule
from app.categorization.normalization import merchant_display_fallback, merchant_key
from app.categorization.providers.chase import find_provider_rule
from app.categorization.types import CategorizationDecision, CategorizationSource
from app.db.models import Category, Tag
from app.imports.types import NormalizedTransaction
from app.rules.evaluation import CompiledWorkspaceRuleSet
from app.rules.loader import load_compiled_rule_set
from app.rules.types import RuleContext


class CategorizationConfigurationError(RuntimeError):
    """Raised when a required built-in category is missing from the database."""


def _category_name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


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


def _builtin_tag_ids(session: Session, names: tuple[str, ...]) -> tuple[int, ...]:
    if not names:
        return ()
    name_keys = {_category_name_key(name) for name in names}
    return tuple(
        session.scalars(
            select(Tag.id)
            .where(Tag.workspace_id.is_(None), Tag.name_key.in_(name_keys))
            .order_by(Tag.name_key, Tag.id)
        )
    )


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
    account_id: int | None = None,
    workspace_rules: CompiledWorkspaceRuleSet | None = None,
) -> CategorizationDecision:
    """Apply workspace, built-in, then safe-fallback precedence to one PR4 candidate."""
    key = merchant_key(candidate.description)
    if workspace_rules is None:
        workspace_rules = load_compiled_rule_set(session, workspace_id)
    if workspace_rules.workspace_id != workspace_id:
        raise ValueError("Compiled workspace rules do not belong to the active workspace.")
    workspace_match = workspace_rules.match(
        RuleContext(
            description=candidate.description,
            merchant_key=key,
            amount_cents=candidate.amount_cents,
            transaction_date=candidate.transaction_date,
            direction=(
                "income"
                if candidate.amount_cents > 0
                else "expense"
                if candidate.amount_cents < 0
                else "zero"
            ),
            account_id=account_id,
            provider_key=provider_key,
        )
    )
    if workspace_match is not None:
        workspace_rule = workspace_match.rule
        return CategorizationDecision(
            normalized_merchant=workspace_rule.normalized_merchant
            or merchant_display_fallback(candidate.description),
            category_id=workspace_rule.category_id,
            is_subscription=workspace_rule.is_subscription,
            source=CategorizationSource.WORKSPACE_RULE,
            tag_ids=workspace_rule.tag_ids,
            billing_period_months=workspace_rule.billing_period_months,
            merchant_rule_id=workspace_rule.id,
            explanation=workspace_match.explanation,
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
            tag_ids=_builtin_tag_ids(session, provider_rule.tag_names),
            billing_period_months=provider_rule.billing_period_months,
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
