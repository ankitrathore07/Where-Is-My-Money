from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.db.models import Category, MerchantRule, Transaction, Workspace
from app.imports.service import (
    ReviewValidationError,
    build_review,
    commit_import,
    create_csv_import,
    save_mapping,
)
from app.imports.storage import LocalUploadStore
from app.imports.types import RowEdit

CSV = b"Date,Description,Amount\n08/01/2026,Netflix.com,-15.99\n"


def _seed_builtins(session: Session) -> dict[str, Category]:
    categories = {
        name: Category(
            workspace_id=None,
            name=name,
            name_key=" ".join(name.split()).casefold(),
            kind=kind,
        )
        for name, kind in BUILTIN_CATEGORY_DEFINITIONS
    }
    session.add_all(categories.values())
    session.commit()
    return categories


def _mapped_job(session: Session, workspace: Workspace, store: LocalUploadStore):
    result = create_csv_import(session, store, workspace, BytesIO(CSV), "retain")
    save_mapping(
        session,
        store,
        result.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    return result.job


def test_builtin_decision_is_visible_in_import_preview(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)

    row = build_review(session, store, job).rows[0]

    assert row.normalized_merchant == "Netflix"
    assert row.category_id == categories["Entertainment"].id
    assert row.category_name == "Entertainment"
    assert row.is_subscription is True
    assert row.categorization_source == "builtin_rule"


def test_workspace_rule_overrides_builtin_without_cross_workspace_leakage(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    tmp_path: Path,
) -> None:
    _seed_builtins(session)
    owned = Category(
        workspace_id=workspace.id,
        name="Household Media",
        name_key="household media",
        kind="expense",
    )
    foreign = Category(
        workspace_id=other_workspace.id,
        name="Foreign Media",
        name_key="foreign media",
        kind="expense",
    )
    session.add_all([owned, foreign])
    session.flush()
    session.add_all(
        [
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Home Streaming",
                category=owned,
                is_subscription=False,
            ),
            MerchantRule(
                workspace_id=other_workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Foreign Streaming",
                category=foreign,
                is_subscription=True,
            ),
        ]
    )
    session.commit()
    store = LocalUploadStore(tmp_path)

    row = build_review(session, store, _mapped_job(session, workspace, store)).rows[0]

    assert row.normalized_merchant == "Home Streaming"
    assert row.category_id == owned.id
    assert row.is_subscription is False
    assert row.categorization_source == "workspace_rule"


def test_commit_preserves_preview_decision_even_if_rule_changes(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            merchant_pattern="NETFLIX COM",
            normalized_merchant="Changed After Preview",
            category_id=categories["Shopping"].id,
            is_subscription=False,
        )
    )
    session.commit()

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant=row.normalized_merchant,
                category_id=row.category_id,
                is_subscription=row.is_subscription,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.normalized_merchant == "Netflix"
    assert transaction.category_id == categories["Entertainment"].id
    assert transaction.is_subscription is True
    assert transaction.categorization_source == "builtin_rule"


def test_review_override_commits_as_manual_without_creating_rule(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    categories = _seed_builtins(session)
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    commit_import(
        session,
        store,
        job,
        (
            RowEdit(
                row.row_number,
                True,
                row.date_value,
                row.description_value,
                row.amount_value,
                normalized_merchant="Movie Night",
                category_id=categories["Shopping"].id,
                is_subscription=False,
                categorization_source=row.categorization_source,
                original_normalized_merchant=row.normalized_merchant,
                original_category_id=row.category_id,
                original_is_subscription=row.is_subscription,
                original_categorization_source=row.categorization_source,
            ),
        ),
    )
    transaction = session.scalar(select(Transaction))

    assert transaction is not None
    assert transaction.description == "Netflix.com"
    assert transaction.normalized_merchant == "Movie Night"
    assert transaction.category_id == categories["Shopping"].id
    assert transaction.is_subscription is False
    assert transaction.categorization_source == "manual"
    assert session.scalar(select(MerchantRule)) is None


def test_import_commit_rejects_category_from_another_workspace(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    tmp_path: Path,
) -> None:
    _seed_builtins(session)
    foreign_category = Category(
        workspace_id=other_workspace.id,
        name="Foreign Only",
        name_key="foreign only",
        kind="expense",
    )
    session.add(foreign_category)
    session.commit()
    store = LocalUploadStore(tmp_path)
    job = _mapped_job(session, workspace, store)
    row = build_review(session, store, job).rows[0]

    with pytest.raises(ReviewValidationError) as error:
        commit_import(
            session,
            store,
            job,
            (
                RowEdit(
                    row.row_number,
                    True,
                    row.date_value,
                    row.description_value,
                    row.amount_value,
                    normalized_merchant=row.normalized_merchant,
                    category_id=foreign_category.id,
                    is_subscription=row.is_subscription,
                    categorization_source=row.categorization_source,
                    original_normalized_merchant=row.normalized_merchant,
                    original_category_id=row.category_id,
                    original_is_subscription=row.is_subscription,
                    original_categorization_source=row.categorization_source,
                ),
            ),
        )

    assert error.value.row_errors[row.row_number] == {
        "category": "Choose a valid categorization value."
    }
    assert session.scalar(select(Transaction)) is None
    assert session.scalar(select(MerchantRule)) is None
