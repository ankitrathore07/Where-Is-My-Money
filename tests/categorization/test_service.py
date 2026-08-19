from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.categorization.service import (
    CategorizationConfigurationError,
    categorize_candidate,
)
from app.categorization.types import CategorizationSource
from app.db.models import Category, MerchantRule, Tag, User, Workspace
from app.imports.types import NormalizedTransaction
from app.rules.loader import load_compiled_rule_set


def _seed_builtin_categories(session: Session) -> dict[str, Category]:
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
    session.flush()
    return categories


def _candidate(description: str, amount_cents: int = -1000) -> NormalizedTransaction:
    return NormalizedTransaction(
        row_number=2,
        transaction_date=date(2026, 8, 9),
        description=description,
        normalized_merchant=description.upper(),
        amount_cents=amount_cents,
    )


def test_workspace_rule_beats_builtin_rule(session: Session, workspace: Workspace) -> None:
    _seed_builtin_categories(session)
    custom = Category(
        workspace_id=workspace.id,
        name="Streaming Treats",
        name_key="streaming treats",
        kind="expense",
    )
    session.add(custom)
    session.flush()
    household = Tag(workspace_id=None, name="Household Expenditure")
    session.add(household)
    session.flush()
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            merchant_pattern="NETFLIX COM",
            normalized_merchant="Streaming",
            category_id=custom.id,
            is_subscription=False,
            billing_period_months=6,
            tags=[household],
        )
    )
    session.commit()

    decision = categorize_candidate(session, workspace.id, _candidate("Netflix.com"))

    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert decision.category_id == custom.id
    assert decision.normalized_merchant == "Streaming"
    assert decision.is_subscription is False
    assert decision.tag_ids == (household.id,)
    assert decision.billing_period_months == 6


def test_builtin_rule_beats_uncategorized(session: Session, workspace: Workspace) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(session, workspace.id, _candidate("Netflix.com"))

    assert decision.source is CategorizationSource.BUILTIN_RULE
    assert decision.category_id == categories["Entertainment"].id
    assert decision.normalized_merchant == "Netflix"
    assert decision.is_subscription is True


def test_provider_rule_beats_builtin_fallback(session: Session, workspace: Workspace) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(
        session,
        workspace.id,
        _candidate("BEST BUY AUTO PYMT 240812 123456789", -2999),
        provider_key="chase_bank_csv",
    )

    assert decision.source is CategorizationSource.PROVIDER_RULE
    assert decision.category_id == categories["Transfers"].id
    assert decision.normalized_merchant == "Best Buy Card Payment"
    assert decision.is_subscription is False


def test_xoom_provider_rule_adds_family_support_tag(session: Session, workspace: Workspace) -> None:
    categories = _seed_builtin_categories(session)
    family_support = Tag(workspace_id=None, name="Family Support")
    session.add(family_support)
    session.flush()

    decision = categorize_candidate(
        session,
        workspace.id,
        _candidate("XOOM DEBIT OID 30178544 WEB ID: 1770510487", -50000),
        provider_key="chase_bank_compact_csv",
    )

    assert decision.source is CategorizationSource.PROVIDER_RULE
    assert decision.category_id == categories["Gifts & Donations"].id
    assert decision.tag_ids == (family_support.id,)


def test_workspace_rule_beats_provider_rule(session: Session, workspace: Workspace) -> None:
    _seed_builtin_categories(session)
    custom = Category(
        workspace_id=workspace.id,
        name="Reviewed Payment",
        name_key="reviewed payment",
        kind="expense",
    )
    session.add(custom)
    session.flush()
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            merchant_pattern="BEST BUY AUTO PYMT",
            normalized_merchant="My Reviewed Payment",
            category=custom,
        )
    )
    session.commit()

    decision = categorize_candidate(
        session,
        workspace.id,
        _candidate("BEST BUY AUTO PYMT"),
        provider_key="chase_bank_csv",
    )

    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert decision.category_id == custom.id
    assert decision.normalized_merchant == "My Reviewed Payment"


def test_compiled_workspace_rule_returns_attribution_and_explanation_before_provider(
    session: Session, workspace: Workspace
) -> None:
    """Break if typed workspace decisions lose attribution or provider precedence wins."""
    _seed_builtin_categories(session)
    custom = Category(
        workspace_id=workspace.id,
        name="Workspace transfer",
        name_key="workspace transfer",
        kind="transfer",
    )
    session.add(custom)
    session.flush()
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Known payment override",
        enabled=True,
        priority=0,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": "contains",
            "value": "BEST BUY AUTO PYMT",
        },
        normalized_merchant="My typed payment",
        category=custom,
    )
    session.add(rule)
    session.commit()
    compiled = load_compiled_rule_set(session, workspace.id)

    decision = categorize_candidate(
        session,
        workspace.id,
        _candidate("BEST BUY AUTO PYMT 240812 123456789", -2999),
        provider_key="chase_bank_csv",
        workspace_rules=compiled,
    )

    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert decision.category_id == custom.id
    assert decision.normalized_merchant == "My typed payment"
    assert decision.merchant_rule_id == rule.id
    assert decision.explanation == "predicate: match"


def test_unconfirmed_chase_pattern_remains_uncategorized(
    session: Session, workspace: Workspace
) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(
        session,
        workspace.id,
        _candidate("UNCONFIRMED MICROSOFT BONUS 123456789", 500000),
        provider_key="chase_bank_csv",
    )

    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == categories["Uncategorized"].id


def test_no_rule_uses_builtin_uncategorized(session: Session, workspace: Workspace) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(session, workspace.id, _candidate("Unknown Shop"))

    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == categories["Uncategorized"].id
    assert decision.normalized_merchant == "Unknown Shop"
    assert decision.is_subscription is False


def test_income_rule_does_not_categorize_outgoing_charge(
    session: Session, workspace: Workspace
) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(session, workspace.id, _candidate("Payroll", -5000))

    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == categories["Uncategorized"].id


def test_income_rule_categorizes_incoming_deposit(session: Session, workspace: Workspace) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(session, workspace.id, _candidate("Payroll", 5000))

    assert decision.source is CategorizationSource.BUILTIN_RULE
    assert decision.category_id == categories["Income"].id


def test_expense_rule_does_not_categorize_incoming_refund(
    session: Session, workspace: Workspace
) -> None:
    categories = _seed_builtin_categories(session)

    decision = categorize_candidate(session, workspace.id, _candidate("Netflix.com", 1599))

    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == categories["Uncategorized"].id
    assert decision.is_subscription is False


def test_same_key_uses_each_workspaces_own_rule(session: Session) -> None:
    owner = User(google_sub="rule-owner", email="rules@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    first_category = Category(
        workspace=first, name="First Choice", name_key="first choice", kind="expense"
    )
    second_category = Category(
        workspace=second, name="Second Choice", name_key="second choice", kind="expense"
    )
    session.add_all([first_category, second_category])
    session.flush()
    _seed_builtin_categories(session)
    session.add_all(
        [
            MerchantRule(
                workspace=first,
                merchant_pattern="LOCAL SHOP",
                normalized_merchant="First Local Shop",
                category=first_category,
            ),
            MerchantRule(
                workspace=second,
                merchant_pattern="LOCAL SHOP",
                normalized_merchant="Second Local Shop",
                category=second_category,
            ),
        ]
    )
    session.commit()

    first_decision = categorize_candidate(session, first.id, _candidate("Local Shop"))
    second_decision = categorize_candidate(session, second.id, _candidate("Local Shop"))

    assert first_decision.category_id == first_category.id
    assert first_decision.normalized_merchant == "First Local Shop"
    assert second_decision.category_id == second_category.id
    assert second_decision.normalized_merchant == "Second Local Shop"


def test_rule_cannot_reference_another_workspaces_category(session: Session) -> None:
    owner = User(google_sub="isolation-owner", email="isolation@example.com")
    first = Workspace(name="First", is_personal=True, owner=owner)
    second = Workspace(name="Second", is_personal=True, owner=owner)
    foreign_category = Category(
        workspace=second, name="Private", name_key="private", kind="expense"
    )
    session.add_all([first, foreign_category])
    session.flush()
    categories = _seed_builtin_categories(session)
    session.add(
        MerchantRule(
            workspace=first,
            merchant_pattern="LOCAL SHOP",
            normalized_merchant="Leaky Shop",
            category=foreign_category,
        )
    )
    session.commit()

    decision = categorize_candidate(session, first.id, _candidate("Local Shop"))

    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == categories["Uncategorized"].id


def test_missing_required_builtin_category_raises_configuration_error(
    session: Session, workspace: Workspace
) -> None:
    with pytest.raises(CategorizationConfigurationError, match="Uncategorized"):
        categorize_candidate(session, workspace.id, _candidate("Unknown Shop"))
