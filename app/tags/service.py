"""Validation and workspace isolation for transaction tags."""

import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import MerchantRule, Tag, Transaction
from app.workspaces.locking import serialize_workspace_mutation

MAX_TAG_NAME_LENGTH = 100


class TagValidationError(ValueError):
    pass


class DuplicateTagNameError(TagValidationError):
    pass


class TagNotFoundError(LookupError):
    pass


class BuiltinTagMutationError(TagValidationError):
    pass


@dataclass(frozen=True)
class TagChoices:
    workspace: tuple[Tag, ...]
    builtin: tuple[Tag, ...]


def normalized_tag_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).split())


def tag_name_key(name: str) -> str:
    return normalized_tag_name(name).casefold()


def _validate_name(name: str) -> tuple[str, str]:
    normalized = normalized_tag_name(name)
    if not normalized:
        raise TagValidationError("Tag name is required.")
    if len(normalized) > MAX_TAG_NAME_LENGTH:
        raise TagValidationError("Tag name must be 100 characters or fewer.")
    return normalized, normalized.casefold()


def list_accessible_tags(session: Session, workspace_id: int) -> TagChoices:
    workspace = tuple(
        session.scalars(
            select(Tag)
            .where(Tag.workspace_id == workspace_id)
            .order_by(func.lower(Tag.name), Tag.id)
        )
    )
    builtin = tuple(
        session.scalars(
            select(Tag).where(Tag.workspace_id.is_(None)).order_by(func.lower(Tag.name), Tag.id)
        )
    )
    return TagChoices(workspace=workspace, builtin=builtin)


def _duplicate_exists(
    session: Session,
    workspace_id: int,
    name_key: str,
    *,
    excluding_id: int | None = None,
) -> bool:
    statement = select(Tag.id).where(
        or_(Tag.workspace_id.is_(None), Tag.workspace_id == workspace_id),
        Tag.name_key == name_key,
    )
    if excluding_id is not None:
        statement = statement.where(Tag.id != excluding_id)
    return session.scalar(statement) is not None


def create_custom_tag(session: Session, workspace_id: int, name: str) -> Tag:
    normalized, name_key = _validate_name(name)
    if _duplicate_exists(session, workspace_id, name_key):
        raise DuplicateTagNameError("A tag with this name already exists.")
    tag = Tag(workspace_id=workspace_id, name=normalized, name_key=name_key)
    session.add(tag)
    session.flush()
    return tag


def _custom_tag(session: Session, workspace_id: int, tag_id: int) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None or (tag.workspace_id is not None and tag.workspace_id != workspace_id):
        raise TagNotFoundError("Tag not found")
    if tag.workspace_id is None:
        raise BuiltinTagMutationError("Built-in tags cannot be changed.")
    return tag


def rename_custom_tag(session: Session, workspace_id: int, tag_id: int, name: str) -> Tag:
    tag = _custom_tag(session, workspace_id, tag_id)
    normalized, name_key = _validate_name(name)
    if _duplicate_exists(session, workspace_id, name_key, excluding_id=tag.id):
        raise DuplicateTagNameError("A tag with this name already exists.")
    tag.name = normalized
    tag.name_key = name_key
    session.flush()
    return tag


def delete_custom_tag(session: Session, workspace_id: int, tag_id: int) -> None:
    if not serialize_workspace_mutation(session, workspace_id):
        raise TagNotFoundError("Tag not found")
    tag = _custom_tag(session, workspace_id, tag_id)
    tag.transactions.clear()
    tag.merchant_rules.clear()
    session.delete(tag)
    session.flush()


def accessible_tags_by_id(
    session: Session, workspace_id: int, tag_ids: tuple[int, ...]
) -> tuple[Tag, ...]:
    unique_ids = tuple(dict.fromkeys(tag_ids))
    if not unique_ids:
        return ()
    tags = tuple(
        session.scalars(
            select(Tag)
            .where(
                Tag.id.in_(unique_ids),
                or_(Tag.workspace_id.is_(None), Tag.workspace_id == workspace_id),
            )
            .order_by(Tag.name_key, Tag.id)
        )
    )
    if len(tags) != len(unique_ids):
        raise TagNotFoundError("Tag not found")
    return tags


def tag_ids_with_subscription(
    session: Session,
    tag_ids: tuple[int, ...],
    is_subscription: bool,
) -> tuple[int, ...]:
    """Keep the legacy subscription field synchronized with its built-in tag."""
    subscription_id = session.scalar(
        select(Tag.id).where(
            Tag.workspace_id.is_(None),
            Tag.name_key == "subscription",
        )
    )
    selected = set(tag_ids)
    if subscription_id is not None:
        if is_subscription:
            selected.add(subscription_id)
        else:
            selected.discard(subscription_id)
    return tuple(selected)


def replace_transaction_tags(
    session: Session,
    workspace_id: int,
    transaction: Transaction,
    tag_ids: tuple[int, ...],
) -> Transaction:
    if transaction.workspace_id != workspace_id:
        raise TagNotFoundError("Transaction not found")
    transaction.tags = list(accessible_tags_by_id(session, workspace_id, tag_ids))
    session.flush()
    return transaction


def replace_rule_tags(
    session: Session,
    workspace_id: int,
    rule: MerchantRule,
    tag_ids: tuple[int, ...],
) -> MerchantRule:
    if rule.workspace_id != workspace_id:
        raise TagNotFoundError("Merchant rule not found")
    rule.tags = list(accessible_tags_by_id(session, workspace_id, tag_ids))
    session.flush()
    return rule
