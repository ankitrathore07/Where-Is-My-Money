from datetime import date, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.transactions.service as transaction_service
from app.categorization.service import categorize_candidate
from app.categorization.types import CategorizationSource
from app.db.models import (
    Category,
    MerchantRule,
    Tag,
    Transaction,
    TransactionCategorizationEvent,
    User,
    Workspace,
)
from app.imports.types import NormalizedTransaction
from app.transactions.service import (
    CategoryNotAccessibleError,
    ManualCategorizationInput,
    ManualCategorizationValidationError,
    MerchantRuleKeyError,
    TransactionNotFoundError,
    manually_categorize_transaction,
    upsert_workspace_rule,
)


def _category(session: Session, workspace_id: int, name: str = "Food") -> Category:
    category = Category(
        workspace_id=workspace_id,
        name=name,
        name_key=name.casefold(),
        kind="expense",
    )
    session.add(category)
    session.flush()
    return category


def _transaction(
    session: Session,
    workspace_id: int,
    category: Category,
    *,
    description: str = "WHOLE FOODS MARKET",
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, 9),
        description=description,
        normalized_merchant=description,
        amount_cents=-4299,
        category=category,
        categorization_source="uncategorized",
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_manual_edit_changes_transaction_but_not_description(
    session: Session, workspace: Workspace
) -> None:
    original = _category(session, workspace.id, "Unsorted")
    selected = _category(session, workspace.id, "Groceries")
    transaction = _transaction(session, workspace.id, original)
    original_description = transaction.description

    updated = manually_categorize_transaction(
        session,
        workspace.id,
        transaction.id,
        ManualCategorizationInput("  Whole   Foods  ", selected.id, False, False),
    )

    assert updated.description == original_description
    assert updated.normalized_merchant == "Whole Foods"
    assert updated.category_id == selected.id
    assert updated.is_subscription is False
    assert updated.categorization_source == "manual"
    event = session.scalar(select(TransactionCategorizationEvent))
    assert event is not None
    assert event.transaction_id == updated.id
    assert event.previous_source == "uncategorized"
    assert event.new_source == "manual"
    assert event.reason == "manual_correction"
    assert session.scalar(select(func.count()).select_from(MerchantRule)) == 0


def test_manual_edit_cannot_use_other_workspace_transaction(session: Session) -> None:
    owner = User(google_sub="transaction-owner", email="transaction-owner@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    session.add_all([first, second])
    session.flush()
    first_category = _category(session, first.id)
    second_transaction = _transaction(session, second.id, _category(session, second.id))
    values = ManualCategorizationInput("Local Shop", first_category.id, False, False)

    with pytest.raises(TransactionNotFoundError):
        manually_categorize_transaction(session, first.id, second_transaction.id, values)


def test_manual_edit_cannot_use_other_workspace_category(session: Session) -> None:
    owner = User(google_sub="category-owner-2", email="category-owner-2@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    session.add_all([first, second])
    session.flush()
    transaction = _transaction(session, first.id, _category(session, first.id))
    foreign_category = _category(session, second.id)
    values = ManualCategorizationInput("Local Shop", foreign_category.id, False, False)

    with pytest.raises(CategoryNotAccessibleError):
        manually_categorize_transaction(session, first.id, transaction.id, values)


@pytest.mark.parametrize(
    ("merchant", "is_subscription", "message"),
    [
        ("   ", False, "required"),
        ("x" * 256, False, "255"),
        ("Local Shop", 1, "boolean"),
    ],
)
def test_manual_edit_validates_merchant_and_subscription(
    session: Session,
    workspace: Workspace,
    merchant: str,
    is_subscription: bool,
    message: str,
) -> None:
    category = _category(session, workspace.id)
    transaction = _transaction(session, workspace.id, category)
    values = ManualCategorizationInput(merchant, category.id, is_subscription, False)

    with pytest.raises(ManualCategorizationValidationError, match=message):
        manually_categorize_transaction(session, workspace.id, transaction.id, values)


def test_save_for_future_upserts_rule_and_keeps_current_manual(
    session: Session, workspace: Workspace
) -> None:
    original = _category(session, workspace.id, "Original")
    selected = _category(session, workspace.id, "Groceries")
    transaction = _transaction(session, workspace.id, original)
    values = ManualCategorizationInput("Whole Foods", selected.id, True, True)

    updated = manually_categorize_transaction(session, workspace.id, transaction.id, values)
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.workspace_id == workspace.id,
            MerchantRule.merchant_pattern == "WHOLE FOODS MARKET",
        )
    )

    assert rule is not None
    assert rule.normalized_merchant == "Whole Foods"
    assert rule.category_id == selected.id
    assert rule.is_subscription is True
    assert updated.categorization_source == "manual"


def test_save_for_future_replaces_only_same_workspace_rule(session: Session) -> None:
    owner = User(google_sub="upsert-owner", email="upsert@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    session.add_all([first, second])
    session.flush()
    first_old = _category(session, first.id, "Old")
    first_new = _category(session, first.id, "New")
    second_category = _category(session, second.id, "Second")
    transaction = _transaction(session, first.id, first_old)
    first_rule = MerchantRule(
        workspace=first,
        merchant_pattern="WHOLE FOODS MARKET",
        normalized_merchant="Old Label",
        category=first_old,
    )
    second_rule = MerchantRule(
        workspace=second,
        merchant_pattern="WHOLE FOODS MARKET",
        normalized_merchant="Second Label",
        category=second_category,
    )
    session.add_all([first_rule, second_rule])
    session.flush()

    manually_categorize_transaction(
        session,
        first.id,
        transaction.id,
        ManualCategorizationInput("New Label", first_new.id, True, True),
    )

    assert first_rule.normalized_merchant == "New Label"
    assert first_rule.category_id == first_new.id
    assert first_rule.is_subscription is True
    assert second_rule.normalized_merchant == "Second Label"
    assert second_rule.category_id == second_category.id


def test_legacy_rule_upsert_serializes_and_versions_only_real_action_changes(
    session: Session, workspace: Workspace
) -> None:
    """Break if a legacy writer bypasses the workspace lock or digest-visible lock version."""
    old_category = _category(session, workspace.id, "Old")
    new_category = _category(session, workspace.id, "New")
    old_tag = Tag(workspace_id=workspace.id, name="Old tag", name_key="old tag")
    new_tag = Tag(workspace_id=workspace.id, name="New tag", name_key="new tag")
    rule = MerchantRule(
        workspace_id=workspace.id,
        merchant_pattern="WHOLE FOODS MARKET",
        name="Whole Foods",
        enabled=True,
        priority=0,
        lock_version=4,
        normalized_merchant="Old merchant",
        category=old_category,
        tags=[old_tag],
    )
    session.add_all((new_tag, rule))
    session.commit()
    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.casefold().split()))

    try:
        updated = upsert_workspace_rule(
            session,
            workspace.id,
            "WHOLE FOODS MARKET",
            "New merchant",
            new_category.id,
            False,
            tag_ids=(new_tag.id,),
            billing_period_months=6,
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

    workspace_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update workspaces")
    )
    rule_lookup_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select merchant_rules")
    )
    assert workspace_lock_index < rule_lookup_index
    assert updated is rule
    assert rule.lock_version == 5
    assert rule.normalized_merchant == "New merchant"
    assert rule.category_id == new_category.id
    assert rule.billing_period_months == 6
    assert [tag.id for tag in rule.tags] == [new_tag.id]

    unchanged = upsert_workspace_rule(
        session,
        workspace.id,
        "WHOLE FOODS MARKET",
        "New merchant",
        new_category.id,
        False,
        tag_ids=(new_tag.id,),
        billing_period_months=6,
    )
    assert unchanged is rule
    assert rule.lock_version == 5


def test_legacy_rule_insert_compacts_existing_order_and_appends_deterministically(
    session: Session, workspace: Workspace
) -> None:
    """Break if legacy inserts keep duplicate/default priority instead of the rule-service order."""
    category = _category(session, workspace.id, "Rules")
    session.add_all(
        (
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="FIRST",
                name="First",
                enabled=True,
                priority=4,
                lock_version=1,
                normalized_merchant="First",
                category=category,
            ),
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="SECOND",
                name="Second",
                enabled=True,
                priority=9,
                lock_version=1,
                normalized_merchant="Second",
                category=category,
            ),
        )
    )
    session.commit()

    inserted = upsert_workspace_rule(
        session,
        workspace.id,
        "THIRD",
        "Third",
        category.id,
        False,
    )
    ordered = tuple(
        session.scalars(
            select(MerchantRule)
            .where(MerchantRule.workspace_id == workspace.id)
            .order_by(MerchantRule.priority, MerchantRule.id)
        )
    )

    assert [rule.merchant_pattern for rule in ordered] == ["FIRST", "SECOND", "THIRD"]
    assert [rule.priority for rule in ordered] == [0, 1, 2]
    assert inserted.priority == 2
    assert inserted.lock_version == 1


def test_save_for_future_locks_workspace_before_loading_or_flushing_transaction(
    session: Session, workspace: Workspace
) -> None:
    """Break if manual-save and confirmation acquire locks in reverse order."""
    category = _category(session, workspace.id, "Groceries")
    transaction = _transaction(session, workspace.id, category)
    session.commit()
    transaction_id = transaction.id
    category_id = category.id
    workspace_id = workspace.id
    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.casefold().split()))

    try:
        manually_categorize_transaction(
            session,
            workspace_id,
            transaction_id,
            ManualCategorizationInput("Whole Foods", category_id, False, True),
        )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

    workspace_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update workspaces")
    )
    transaction_lookup_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select transactions")
    )
    transaction_flush_indexes = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update transactions")
    ]
    assert workspace_lock_index < transaction_lookup_index
    assert all(workspace_lock_index < index for index in transaction_flush_indexes)


def test_not_saving_for_future_leaves_existing_rule_unchanged(
    session: Session, workspace: Workspace
) -> None:
    original = _category(session, workspace.id, "Original")
    selected = _category(session, workspace.id, "Selected")
    transaction = _transaction(session, workspace.id, original)
    rule = MerchantRule(
        workspace_id=workspace.id,
        merchant_pattern="WHOLE FOODS MARKET",
        normalized_merchant="Existing",
        category=original,
        is_subscription=False,
    )
    session.add(rule)
    session.flush()

    manually_categorize_transaction(
        session,
        workspace.id,
        transaction.id,
        ManualCategorizationInput("Current Only", selected.id, True, False),
    )

    assert rule.normalized_merchant == "Existing"
    assert rule.category_id == original.id
    assert rule.is_subscription is False


def test_saved_rule_applies_only_to_later_candidate(session: Session, workspace: Workspace) -> None:
    category = _category(session, workspace.id, "Groceries")
    edited = _transaction(session, workspace.id, category)
    historical = _transaction(session, workspace.id, category)
    manually_categorize_transaction(
        session,
        workspace.id,
        edited.id,
        ManualCategorizationInput("Whole Foods", category.id, True, True),
    )

    decision = categorize_candidate(
        session,
        workspace.id,
        NormalizedTransaction(
            row_number=2,
            transaction_date=date(2026, 8, 10),
            description=edited.description,
            normalized_merchant=edited.description,
            amount_cents=-5000,
        ),
    )

    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert decision.category_id == category.id
    assert decision.is_subscription is True
    assert edited.categorization_source == "manual"
    assert historical.categorization_source == "uncategorized"


def test_save_for_future_rejects_description_without_merchant_key(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, workspace.id)
    transaction = _transaction(session, workspace.id, category, description="***")

    with pytest.raises(MerchantRuleKeyError, match="merchant key"):
        manually_categorize_transaction(
            session,
            workspace.id,
            transaction.id,
            ManualCategorizationInput("Symbols", category.id, False, True),
        )


def test_save_for_future_rejects_merchant_key_longer_than_rule_schema(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, workspace.id)
    transaction = _transaction(session, workspace.id, category, description="A" * 256)

    with pytest.raises(MerchantRuleKeyError, match="255"):
        manually_categorize_transaction(
            session,
            workspace.id,
            transaction.id,
            ManualCategorizationInput("Long Merchant", category.id, False, True),
        )

    assert transaction.normalized_merchant == "A" * 256
    assert session.scalar(select(MerchantRule)) is None


def test_rule_failure_can_roll_back_transaction_and_rule_atomically(
    session: Session, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _category(session, workspace.id, "Original")
    selected = _category(session, workspace.id, "Selected")
    transaction = _transaction(session, workspace.id, original)
    session.commit()
    original_id = original.id

    def fail_rule_upsert(*args, **kwargs):
        raise IntegrityError("forced", {}, RuntimeError("forced"))

    monkeypatch.setattr(transaction_service, "upsert_workspace_rule", fail_rule_upsert)
    with pytest.raises(IntegrityError):
        manually_categorize_transaction(
            session,
            workspace.id,
            transaction.id,
            ManualCategorizationInput("Changed", selected.id, True, True),
        )
    session.rollback()
    session.refresh(transaction)

    assert transaction.normalized_merchant == "WHOLE FOODS MARKET"
    assert transaction.category_id == original_id
    assert transaction.is_subscription is False
    assert transaction.categorization_source == "uncategorized"


def test_manual_edit_assigns_multiple_tags_and_cadence_to_transaction_and_rule(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, workspace.id, "Insurance")
    transaction = _transaction(session, workspace.id, category, description="VEHICLE POLICY")
    household = Tag(workspace_id=workspace.id, name="Household Expenditure")
    vehicle = Tag(workspace_id=None, name="Vehicle")
    subscription = Tag(workspace_id=None, name="Subscription")
    session.add_all((household, vehicle, subscription))
    session.flush()

    updated = manually_categorize_transaction(
        session,
        workspace.id,
        transaction.id,
        ManualCategorizationInput(
            "Vehicle Insurance",
            category.id,
            True,
            True,
            tag_ids=(vehicle.id, household.id),
            billing_period_months=6,
        ),
    )
    rule = session.scalar(select(MerchantRule))

    assert rule is not None
    assert [tag.name for tag in updated.tags] == [
        "Household Expenditure",
        "Subscription",
        "Vehicle",
    ]
    assert [tag.name for tag in rule.tags] == [
        "Household Expenditure",
        "Subscription",
        "Vehicle",
    ]
    assert updated.billing_period_months == 6
    assert rule.billing_period_months == 6


@pytest.mark.parametrize("billing_period_months", [0, 121, True])
def test_manual_edit_rejects_invalid_billing_period(
    session: Session,
    workspace: Workspace,
    billing_period_months: int,
) -> None:
    category = _category(session, workspace.id)
    transaction = _transaction(session, workspace.id, category)

    with pytest.raises(ManualCategorizationValidationError, match="billing"):
        manually_categorize_transaction(
            session,
            workspace.id,
            transaction.id,
            ManualCategorizationInput(
                "Merchant",
                category.id,
                False,
                False,
                billing_period_months=billing_period_months,
            ),
        )
