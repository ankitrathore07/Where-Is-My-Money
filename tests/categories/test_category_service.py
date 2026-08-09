import pytest
from sqlalchemy.orm import Session

from app.categories.service import (
    CategoryValidationError,
    DuplicateCategoryNameError,
    category_name_key,
    create_custom_category,
    list_accessible_categories,
)
from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.db.models import Category, User, Workspace


def test_category_name_key_normalizes_unicode_case_and_whitespace() -> None:
    assert category_name_key("  Ｔrips   &   CAFÉ  ") == "trips & café"


@pytest.mark.parametrize("kind", ["expense", "income", "transfer"])
def test_create_custom_category_trims_name_and_accepts_supported_kinds(
    session: Session, workspace: Workspace, kind: str
) -> None:
    category = create_custom_category(session, workspace.id, "  Weekend   Trips  ", kind)

    assert category.workspace_id == workspace.id
    assert category.name == "Weekend Trips"
    assert category.name_key == "weekend trips"
    assert category.kind == kind


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("   ", "required"),
        ("x" * 101, "100"),
    ],
)
def test_create_custom_category_rejects_invalid_name(
    session: Session, workspace: Workspace, name: str, message: str
) -> None:
    with pytest.raises(CategoryValidationError, match=message) as exc_info:
        create_custom_category(session, workspace.id, name, "expense")

    assert exc_info.value.field == "name"


def test_create_custom_category_rejects_invalid_kind(
    session: Session, workspace: Workspace
) -> None:
    with pytest.raises(CategoryValidationError, match="expense, income, or transfer") as exc_info:
        create_custom_category(session, workspace.id, "Trips", "other")

    assert exc_info.value.field == "kind"


def test_duplicate_category_name_is_case_insensitive_in_one_workspace(
    session: Session, workspace: Workspace
) -> None:
    create_custom_category(session, workspace.id, "Trips", "expense")

    with pytest.raises(DuplicateCategoryNameError, match="already exists"):
        create_custom_category(session, workspace.id, "  TRIPS ", "expense")


def test_same_category_name_is_allowed_in_different_workspaces(session: Session) -> None:
    owner = User(google_sub="category-owner", email="categories@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    session.add_all([first, second])
    session.flush()

    first_category = create_custom_category(session, first.id, "Trips", "expense")
    second_category = create_custom_category(session, second.id, "TRIPS", "expense")

    assert first_category.workspace_id == first.id
    assert second_category.workspace_id == second.id


def test_list_accessible_categories_excludes_other_workspace(session: Session) -> None:
    owner = User(google_sub="list-owner", email="category-list@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    first_custom = Category(
        workspace=first, name="First Custom", name_key="first custom", kind="expense"
    )
    second_custom = Category(
        workspace=second, name="Second Custom", name_key="second custom", kind="expense"
    )
    builtin = Category(
        workspace_id=None,
        name="Uncategorized",
        name_key="uncategorized",
        kind="expense",
    )
    session.add_all([first_custom, second_custom, builtin])
    session.flush()

    choices = list_accessible_categories(session, first.id)

    assert choices.workspace == (first_custom,)
    assert choices.builtin == (builtin,)
    assert second_custom not in choices.workspace


def test_seeded_catalog_contract_contains_exactly_approved_categories(session: Session) -> None:
    session.add_all(
        Category(
            workspace_id=None,
            name=name,
            name_key=category_name_key(name),
            kind=kind,
        )
        for name, kind in BUILTIN_CATEGORY_DEFINITIONS
    )
    session.flush()

    choices = list_accessible_categories(session, workspace_id=999)

    assert {(category.name, category.kind) for category in choices.builtin} == set(
        BUILTIN_CATEGORY_DEFINITIONS
    )
