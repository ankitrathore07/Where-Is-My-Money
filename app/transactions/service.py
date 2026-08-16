"""Workspace-scoped transaction categorization mutations."""

import unicodedata
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.categorization.normalization import MAX_MERCHANT_LENGTH, merchant_key
from app.categorization.types import CategorizationSource
from app.db.models import Category, MerchantRule, Transaction
from app.rules.service import list_rules, serialize_workspace_rule_mutation
from app.tags.service import (
    replace_rule_tags,
    replace_transaction_tags,
    tag_ids_with_subscription,
)


class TransactionNotFoundError(LookupError):
    pass


class CategoryNotAccessibleError(LookupError):
    pass


class ManualCategorizationValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class MerchantRuleKeyError(ValueError):
    pass


@dataclass(frozen=True)
class ManualCategorizationInput:
    normalized_merchant: str
    category_id: int
    is_subscription: bool
    save_for_future: bool
    tag_ids: tuple[int, ...] = ()
    billing_period_months: int | None = None


def get_transaction_for_categorization(
    session: Session,
    workspace_id: int,
    transaction_id: int,
    *,
    lock: bool = False,
) -> Transaction:
    """Load a transaction only through its active workspace boundary."""
    statement = select(Transaction).where(
        Transaction.id == transaction_id,
        Transaction.workspace_id == workspace_id,
    )
    if lock:
        statement = statement.with_for_update()
    transaction = session.scalar(statement)
    if transaction is None:
        raise TransactionNotFoundError("Transaction not found")
    return transaction


def _merchant_display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _validated_values(
    values: ManualCategorizationInput,
) -> tuple[str, bool, bool, tuple[int, ...], int | None]:
    normalized_merchant = _merchant_display(values.normalized_merchant)
    if not normalized_merchant:
        raise ManualCategorizationValidationError(
            "normalized_merchant", "Merchant name is required."
        )
    if len(normalized_merchant) > MAX_MERCHANT_LENGTH:
        raise ManualCategorizationValidationError(
            "normalized_merchant", "Merchant name must be 255 characters or fewer."
        )
    if type(values.is_subscription) is not bool:
        raise ManualCategorizationValidationError(
            "is_subscription", "Subscription must be a boolean."
        )
    if type(values.save_for_future) is not bool:
        raise ManualCategorizationValidationError(
            "save_for_future", "Save for future must be a boolean."
        )
    if any(type(tag_id) is not int or tag_id <= 0 for tag_id in values.tag_ids):
        raise ManualCategorizationValidationError("tags", "Choose valid tags.")
    billing_period_months = values.billing_period_months
    if billing_period_months is not None and (
        type(billing_period_months) is not int
        or billing_period_months < 1
        or billing_period_months > 120
    ):
        raise ManualCategorizationValidationError(
            "billing_period_months",
            "Choose a billing cadence between 1 and 120 months.",
        )
    return (
        normalized_merchant,
        values.is_subscription,
        values.save_for_future,
        values.tag_ids,
        billing_period_months,
    )


def upsert_workspace_rule(
    session: Session,
    workspace_id: int,
    key: str,
    normalized_merchant: str,
    category_id: int,
    is_subscription: bool,
    *,
    tag_ids: tuple[int, ...] = (),
    billing_period_months: int | None = None,
) -> MerchantRule:
    """Create or replace one exact merchant rule inside the active workspace."""
    serialize_workspace_rule_mutation(session, workspace_id)
    effective_tag_ids = tag_ids_with_subscription(session, tag_ids, is_subscription)
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.merchant_pattern == key,
        )
    )
    if rule is None:
        ordered = list(list_rules(session, workspace_id))
        for priority, existing_rule in enumerate(ordered):
            existing_rule.priority = priority
        rule = MerchantRule(
            workspace_id=workspace_id,
            merchant_pattern=key,
            enabled=True,
            priority=len(ordered),
            condition_version=1,
            condition_json={},
            lock_version=1,
            normalized_merchant=normalized_merchant,
            category_id=category_id,
            is_subscription=is_subscription,
            billing_period_months=billing_period_months,
        )
        session.add(rule)
        session.flush()
        replace_rule_tags(session, workspace_id, rule, effective_tag_ids)
    else:
        current_tag_ids = {tag.id for tag in rule.tags}
        actions_changed = (
            rule.normalized_merchant != normalized_merchant
            or rule.category_id != category_id
            or rule.is_subscription != is_subscription
            or rule.billing_period_months != billing_period_months
            or current_tag_ids != set(effective_tag_ids)
        )
        rule.normalized_merchant = normalized_merchant
        rule.category_id = category_id
        rule.is_subscription = is_subscription
        rule.billing_period_months = billing_period_months
        if actions_changed:
            rule.lock_version += 1
            replace_rule_tags(session, workspace_id, rule, effective_tag_ids)
        else:
            session.flush()
    return rule


def manually_categorize_transaction(
    session: Session,
    workspace_id: int,
    transaction_id: int,
    values: ManualCategorizationInput,
) -> Transaction:
    """Apply a scoped manual update and optionally upsert one exact-key rule."""
    (
        normalized_merchant,
        is_subscription,
        save_for_future,
        tag_ids,
        billing_period_months,
    ) = _validated_values(values)
    if save_for_future:
        serialize_workspace_rule_mutation(session, workspace_id)
    transaction = get_transaction_for_categorization(
        session,
        workspace_id,
        transaction_id,
        lock=save_for_future,
    )

    category = session.scalar(
        select(Category).where(
            Category.id == values.category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    if category is None:
        raise CategoryNotAccessibleError("Category not found")

    key = merchant_key(transaction.description)
    if save_for_future and not key:
        raise MerchantRuleKeyError("Transaction description has no usable merchant key")
    if save_for_future and len(key) > MAX_MERCHANT_LENGTH:
        raise MerchantRuleKeyError("Future-rule merchant key must be 255 characters or fewer")

    transaction.normalized_merchant = normalized_merchant
    transaction.category_id = category.id
    transaction.is_subscription = is_subscription
    transaction.billing_period_months = billing_period_months
    transaction.categorization_source = CategorizationSource.MANUAL.value
    replace_transaction_tags(
        session,
        workspace_id,
        transaction,
        tag_ids_with_subscription(session, tag_ids, is_subscription),
    )

    if save_for_future:
        upsert_workspace_rule(
            session,
            workspace_id,
            key,
            normalized_merchant,
            category.id,
            is_subscription,
            tag_ids=tag_ids,
            billing_period_months=billing_period_months,
        )
    else:
        session.flush()
    return transaction
