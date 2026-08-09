"""Validation and workspace isolation for custom transaction categories."""

import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Category

MAX_CATEGORY_NAME_LENGTH = 100
ALLOWED_CATEGORY_KINDS = frozenset({"expense", "income", "transfer"})


class CategoryValidationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class DuplicateCategoryNameError(CategoryValidationError):
    def __init__(self) -> None:
        super().__init__("name", "A category with this name already exists in this workspace.")


@dataclass(frozen=True)
class CategoryChoices:
    workspace: tuple[Category, ...]
    builtin: tuple[Category, ...]


def _normalized_category_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).split())


def category_name_key(name: str) -> str:
    """Return the Unicode-normalized, collapsed, case-insensitive category key."""
    return _normalized_category_name(name).casefold()


def list_accessible_categories(session: Session, workspace_id: int) -> CategoryChoices:
    """List global built-ins and only the active workspace's custom categories."""
    workspace = tuple(
        session.scalars(
            select(Category)
            .where(Category.workspace_id == workspace_id)
            .order_by(func.lower(Category.name), Category.id)
        )
    )
    builtin = tuple(
        session.scalars(
            select(Category)
            .where(Category.workspace_id.is_(None))
            .order_by(func.lower(Category.name), Category.id)
        )
    )
    return CategoryChoices(workspace=workspace, builtin=builtin)


def create_custom_category(session: Session, workspace_id: int, name: str, kind: str) -> Category:
    """Validate, flush, and return one workspace-owned custom category."""
    normalized_name = _normalized_category_name(name)
    if not normalized_name:
        raise CategoryValidationError("name", "Category name is required.")
    if len(normalized_name) > MAX_CATEGORY_NAME_LENGTH:
        raise CategoryValidationError("name", "Category name must be 100 characters or fewer.")
    if kind not in ALLOWED_CATEGORY_KINDS:
        raise CategoryValidationError("kind", "Choose expense, income, or transfer.")

    name_key = category_name_key(normalized_name)
    duplicate_id = session.scalar(
        select(Category.id).where(
            Category.workspace_id == workspace_id,
            Category.name_key == name_key,
        )
    )
    if duplicate_id is not None:
        raise DuplicateCategoryNameError

    category = Category(
        workspace_id=workspace_id,
        name=normalized_name,
        name_key=name_key,
        kind=kind,
    )
    session.add(category)
    session.flush()
    return category
