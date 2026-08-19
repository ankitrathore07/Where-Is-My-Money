from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.categorization.types import CategorizationSource
from app.db.models import Category, MerchantRule, Transaction, Workspace
from app.rules.presentation import transaction_explanation


def _transaction(
    session: Session,
    workspace_id: int,
    *,
    source: CategorizationSource,
    rule: MerchantRule | None = None,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, 15, tzinfo=UTC),
        description="COFFEE SHOP",
        amount_cents=-500,
        categorization_source=source.value,
        merchant_rule=rule,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_linked_workspace_rule_explanation_names_and_links_the_rule(
    session: Session,
    workspace: Workspace,
) -> None:
    category = Category(
        workspace_id=workspace.id,
        name="Coffee",
        name_key="coffee",
        kind="expense",
    )
    session.add(category)
    session.flush()
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Coffee shops",
        enabled=True,
        priority=0,
        condition_version=1,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": "contains",
            "value": "COFFEE",
        },
        lock_version=1,
        category_id=category.id,
    )
    session.add(rule)
    session.flush()

    explanation = transaction_explanation(
        _transaction(
            session,
            workspace.id,
            source=CategorizationSource.WORKSPACE_RULE,
            rule=rule,
        )
    )

    assert explanation.source_label == "Workspace rule"
    assert explanation.rule_id == rule.id
    assert explanation.rule_name == "Coffee shops"
    assert explanation.condition_summary == "description contains “COFFEE”"


def test_deleted_rule_transaction_is_presented_truthfully(
    session: Session,
    workspace: Workspace,
) -> None:
    transaction = _transaction(
        session,
        workspace.id,
        source=CategorizationSource.WORKSPACE_RULE,
    )

    explanation = transaction_explanation(transaction)

    assert explanation.source_label == "Deleted workspace rule"
    assert explanation.rule_id is None
    assert "deleted" in explanation.detail.casefold()
