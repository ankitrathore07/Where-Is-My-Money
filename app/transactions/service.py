"""Workspace-scoped transaction categorization mutations."""

import unicodedata
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.categorization.normalization import MAX_MERCHANT_LENGTH, merchant_key
from app.categorization.types import CategorizationSource
from app.db.models import Category, MerchantRule, Transaction


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


def get_transaction_for_categorization(
    session: Session, workspace_id: int, transaction_id: int
) -> Transaction:
    """Load a transaction only through its active workspace boundary."""
    transaction = session.scalar(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == workspace_id,
        )
    )
    if transaction is None:
        raise TransactionNotFoundError("Transaction not found")
    return transaction


def _merchant_display(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _validated_values(values: ManualCategorizationInput) -> tuple[str, bool, bool]:
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
    return normalized_merchant, values.is_subscription, values.save_for_future


def upsert_workspace_rule(
    session: Session,
    workspace_id: int,
    key: str,
    normalized_merchant: str,
    category_id: int,
    is_subscription: bool,
) -> MerchantRule:
    """Create or replace one exact merchant rule inside the active workspace."""
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.merchant_pattern == key,
        )
    )
    if rule is None:
        rule = MerchantRule(
            workspace_id=workspace_id,
            merchant_pattern=key,
            normalized_merchant=normalized_merchant,
            category_id=category_id,
            is_subscription=is_subscription,
        )
        session.add(rule)
    else:
        rule.normalized_merchant = normalized_merchant
        rule.category_id = category_id
        rule.is_subscription = is_subscription
    session.flush()
    return rule


def manually_categorize_transaction(
    session: Session,
    workspace_id: int,
    transaction_id: int,
    values: ManualCategorizationInput,
) -> Transaction:
    """Apply a scoped manual update and optionally upsert one exact-key rule."""
    transaction = get_transaction_for_categorization(session, workspace_id, transaction_id)

    category = session.scalar(
        select(Category).where(
            Category.id == values.category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    if category is None:
        raise CategoryNotAccessibleError("Category not found")

    normalized_merchant, is_subscription, save_for_future = _validated_values(values)
    key = merchant_key(transaction.description)
    if save_for_future and not key:
        raise MerchantRuleKeyError("Transaction description has no usable merchant key")
    if save_for_future and len(key) > MAX_MERCHANT_LENGTH:
        raise MerchantRuleKeyError("Future-rule merchant key must be 255 characters or fewer")

    transaction.normalized_merchant = normalized_merchant
    transaction.category_id = category.id
    transaction.is_subscription = is_subscription
    transaction.categorization_source = CategorizationSource.MANUAL.value

    if save_for_future:
        upsert_workspace_rule(
            session,
            workspace_id,
            key,
            normalized_merchant,
            category.id,
            is_subscription,
        )
    else:
        session.flush()
    return transaction
