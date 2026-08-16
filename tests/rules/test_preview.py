from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.categorization.types import CategorizationSource
from app.db.models import (
    Account,
    Category,
    ImportJob,
    MerchantRule,
    Tag,
    Transaction,
    Workspace,
)
from app.rules.service import RuleDraft, preview_rule_impact, simulate_rules
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
    RuleContext,
)
from app.rules.validation import condition_to_json


def _category(session: Session, workspace_id: int, name: str) -> Category:
    category = Category(
        workspace_id=workspace_id,
        name=name,
        name_key=name.casefold(),
        kind="expense",
    )
    session.add(category)
    session.flush()
    return category


def _rule(
    workspace_id: int,
    category_id: int | None,
    *,
    name: str,
    priority: int,
    value: str,
    operator: str = "exact",
) -> MerchantRule:
    return MerchantRule(
        workspace_id=workspace_id,
        name=name,
        enabled=True,
        priority=priority,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": operator,
            "value": value,
        },
        normalized_merchant=name,
        category_id=category_id,
    )


def _typed_rule(
    workspace_id: int,
    category_id: int,
    *,
    name: str,
    priority: int,
    condition: ConditionNode,
) -> MerchantRule:
    return MerchantRule(
        workspace_id=workspace_id,
        name=name,
        enabled=True,
        priority=priority,
        condition_json=json.loads(condition_to_json(condition)),
        normalized_merchant=name,
        category_id=category_id,
    )


def _transaction(
    workspace_id: int,
    *,
    description: str,
    category_id: int,
    account_job_id: int | None = None,
    merchant: str = "Old merchant",
    source: str = CategorizationSource.UNCATEGORIZED.value,
    tags: tuple[Tag, ...] = (),
    is_subscription: bool = False,
    cadence: int | None = None,
) -> Transaction:
    return Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, 15, tzinfo=UTC),
        description=description,
        normalized_merchant=merchant,
        amount_cents=-1_250,
        category_id=category_id,
        categorization_source=source,
        is_subscription=is_subscription,
        billing_period_months=cadence,
        import_job_id=account_job_id,
        tags=list(tags),
    )


def _seed_preview_scenario(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> tuple[MerchantRule, RuleDraft, Category, Account]:
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Coffee")
    higher_category = _category(session, workspace.id, "VIP")
    foreign_current = _category(session, other_workspace.id, "Foreign current")
    tag = Tag(workspace_id=workspace.id, name="Morning")
    account = Account(
        workspace_id=workspace.id,
        name="Household checking",
        account_type="checking",
        institution_key="other",
        institution="Local",
        is_liability=False,
    )
    session.add_all([tag, account])
    session.flush()
    job = ImportJob(workspace_id=workspace.id, account_id=account.id, status="committed")
    session.add(job)
    session.flush()
    higher = _rule(
        workspace.id,
        higher_category.id,
        name="VIP winner",
        priority=0,
        value="COFFEE VIP",
    )
    session.add(higher)
    session.add_all(
        [
            _transaction(
                workspace.id,
                description="COFFEE SHOP REF 123456789",
                category_id=current.id,
                account_job_id=job.id,
            ),
            _transaction(
                workspace.id,
                description="COFFEE CART",
                category_id=target.id,
                account_job_id=job.id,
            ),
            _transaction(
                workspace.id,
                description="COFFEE VIP",
                category_id=current.id,
                account_job_id=job.id,
                source=CategorizationSource.MANUAL.value,
            ),
            _transaction(
                workspace.id,
                description="COFFEE VIP",
                category_id=current.id,
                account_job_id=job.id,
            ),
            _transaction(
                workspace.id,
                description="COFFEE SAME",
                category_id=target.id,
                account_job_id=job.id,
                merchant="Coffee Club",
                tags=(tag,),
                is_subscription=True,
                cadence=1,
            ),
            _transaction(
                other_workspace.id,
                description="COFFEE FOREIGN",
                category_id=foreign_current.id,
            ),
        ]
    )
    session.commit()
    draft = RuleDraft(
        name="Coffee draft",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Coffee Club",
        category_id=target.id,
        tag_ids=(tag.id,),
        is_subscription=True,
        billing_period_months=1,
    )
    return higher, draft, current, account


def test_preview_reports_changes_manual_protection_shadowing_and_safe_groups(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    """Break if preview crosses workspaces, overwrites manual choices, or hides a winner."""
    higher, draft, current, account = _seed_preview_scenario(session, workspace, other_workspace)

    statements: list[str] = []

    def record_writes(
        _conn: object, _cursor: object, statement: str, _params: object, _ctx: object, _many: bool
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            statements.append(statement)

    assert session.bind is not None
    event.listen(session.bind, "before_cursor_execute", record_writes)
    try:
        preview = preview_rule_impact(
            session,
            workspace.id,
            draft,
            exclude_rule_id=None,
        )
    finally:
        event.remove(session.bind, "before_cursor_execute", record_writes)

    assert preview.matched_count == 5
    assert preview.would_change_count == 2
    assert preview.unchanged_count == 1
    assert preview.manual_skip_count == 1
    assert preview.conflict_skip_count == 1
    assert preview.not_matched_count == 0
    assert (
        preview.would_change_count
        + preview.unchanged_count
        + preview.manual_skip_count
        + preview.conflict_skip_count
        == preview.matched_count
    )
    assert preview.conflicts[0].winning_rule_id == higher.id
    assert preview.conflicts[0].count == 1
    assert [(item.group_id, item.count) for item in preview.category_counts] == [
        (current.id, 3),
        (draft.category_id, 2),
    ]
    assert [(item.group_id, item.count) for item in preview.account_counts] == [(account.id, 5)]
    assert len(preview.examples) <= 20
    assert any(example.description == "COFFEE SHOP REF" for example in preview.examples)
    assert all("123456789" not in example.description for example in preview.examples)
    assert statements == []


def test_preview_caps_examples_without_capping_exact_counts(
    session: Session, workspace: Workspace
) -> None:
    """Break if UI examples grow without bound or the cap truncates aggregate counts."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    session.add_all(
        [
            _transaction(
                workspace.id,
                description=f"BULK COFFEE {index} 123456789",
                category_id=current.id,
            )
            for index in range(25)
        ]
    )
    session.commit()
    draft = RuleDraft(
        name="Bulk",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Coffee",
        category_id=target.id,
    )

    preview = preview_rule_impact(session, workspace.id, draft, exclude_rule_id=None)

    assert preview.would_change_count == 25
    assert len(preview.examples) == 20


@pytest.mark.parametrize(
    "condition",
    [
        PredicateCondition("provider_key", "equal", "generic_csv"),
        NotCondition(PredicateCondition("provider_key", "equal", "generic_csv")),
        AllCondition(
            (
                PredicateCondition("description", "contains", "COFFEE"),
                NotCondition(PredicateCondition("provider_key", "equal", "generic_csv")),
            )
        ),
    ],
)
def test_preview_surfaces_unknown_provider_in_draft_without_false_matches(
    session: Session,
    workspace: Workspace,
    condition: ConditionNode,
) -> None:
    """Break if direct or negated unknown provider provenance becomes true or false."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    session.add(_transaction(workspace.id, description="COFFEE", category_id=current.id))
    session.commit()
    draft = RuleDraft(
        name="Provider draft", condition=condition, normalized_merchant=None, category_id=target.id
    )

    preview = preview_rule_impact(session, workspace.id, draft, exclude_rule_id=None)

    assert preview.matched_count == 0
    assert preview.not_matched_count == 0
    assert preview.unavailable_count == 1
    assert preview.limitation_codes == ("historical_provider_unavailable",)
    assert preview.would_change_count == 0
    assert preview.conflict_skip_count == 0
    assert preview.examples == ()


@pytest.mark.parametrize(
    "higher_condition",
    [
        PredicateCondition("provider_key", "equal", "generic_csv"),
        NotCondition(PredicateCondition("provider_key", "equal", "generic_csv")),
        AnyCondition(
            (
                PredicateCondition("description", "exact", "NEVER"),
                NotCondition(PredicateCondition("provider_key", "equal", "generic_csv")),
            )
        ),
    ],
)
def test_preview_does_not_guess_higher_priority_provider_conflict_winner(
    session: Session,
    workspace: Workspace,
    higher_condition: ConditionNode,
) -> None:
    """Break if an unknown higher rule is ignored or reported as a definite winner."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    session.add(
        _typed_rule(
            workspace.id,
            current.id,
            name="Unknown higher",
            priority=0,
            condition=higher_condition,
        )
    )
    session.add(_transaction(workspace.id, description="COFFEE", category_id=current.id))
    session.commit()
    draft = RuleDraft(
        name="Coffee draft",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Coffee",
        category_id=target.id,
    )

    preview = preview_rule_impact(session, workspace.id, draft, exclude_rule_id=None)

    assert preview.matched_count == 1
    assert preview.unavailable_count == 1
    assert preview.limitation_codes == ("historical_provider_unavailable",)
    assert preview.would_change_count == 0
    assert preview.conflict_skip_count == 0
    assert preview.conflicts == ()
    assert preview.examples == ()


def test_preview_keeps_inaccessible_aggregates_separate_without_exposing_resources(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    """Break if corrupt foreign references merge with legitimate empty aggregate buckets."""
    target = _category(session, workspace.id, "Target")
    foreign_category = _category(session, other_workspace.id, "Secret foreign category")
    foreign_account = Account(
        workspace_id=other_workspace.id,
        name="Secret foreign account",
        account_type="checking",
        institution_key="other",
        institution="Elsewhere",
        is_liability=False,
    )
    session.add(foreign_account)
    session.flush()
    corrupt_job = ImportJob(
        workspace_id=workspace.id,
        account_id=foreign_account.id,
        status="committed",
    )
    session.add(corrupt_job)
    session.flush()
    session.add_all(
        [
            _transaction(
                workspace.id,
                description="COFFEE FOREIGN REFERENCES",
                category_id=foreign_category.id,
                account_job_id=corrupt_job.id,
            ),
            _transaction(
                workspace.id,
                description="COFFEE LEGITIMATE EMPTY",
                category_id=None,
            ),
        ]
    )
    session.commit()
    draft = RuleDraft(
        name="Coffee draft",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Coffee",
        category_id=target.id,
    )

    preview = preview_rule_impact(session, workspace.id, draft, exclude_rule_id=None)

    assert {item.label: item.count for item in preview.category_counts} == {
        "Uncategorized": 1,
        "Unavailable category": 1,
    }
    assert {item.label: item.count for item in preview.account_counts} == {
        "No account": 1,
        "Unavailable account": 1,
    }
    assert all(item.group_id is None for item in preview.category_counts)
    assert all(item.group_id is None for item in preview.account_counts)
    assert "Secret foreign category" not in repr(preview)
    assert "Secret foreign account" not in repr(preview)


def test_edit_preview_replaces_rule_at_its_priority_and_ignores_lower_matches(
    session: Session, workspace: Workspace
) -> None:
    """Break if editing is falsely shadowed by the old rule or a lower-priority rule."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    session.add(_rule(workspace.id, target.id, name="Higher miss", priority=0, value="OTHER"))
    edited = _rule(
        workspace.id,
        current.id,
        name="Old target",
        priority=1,
        value="COFFEE",
        operator="contains",
    )
    lower = _rule(
        workspace.id,
        current.id,
        name="Lower match",
        priority=2,
        value="COFFEE",
        operator="contains",
    )
    session.add_all([edited, lower])
    session.add(_transaction(workspace.id, description="COFFEE SHOP", category_id=current.id))
    session.commit()
    draft = RuleDraft(
        name="Edited target",
        condition=PredicateCondition("description", "contains", "COFFEE"),
        normalized_merchant="Edited",
        category_id=target.id,
    )

    preview = preview_rule_impact(
        session,
        workspace.id,
        draft,
        exclude_rule_id=edited.id,
    )

    assert preview.would_change_count == 1
    assert preview.conflict_skip_count == 0
    assert preview.conflicts == ()


def test_simulator_returns_every_matching_rule_in_order_and_the_winning_decision(
    session: Session, workspace: Workspace
) -> None:
    """Break if simulation returns only the winner or mutates precedence ordering."""
    first_category = _category(session, workspace.id, "First")
    second_category = _category(session, workspace.id, "Second")
    first = _rule(
        workspace.id,
        first_category.id,
        name="First winner",
        priority=0,
        value="COFFEE",
        operator="contains",
    )
    second = _rule(
        workspace.id,
        second_category.id,
        name="Second match",
        priority=1,
        value="COFFEE",
        operator="contains",
    )
    session.add_all([first, second])
    session.commit()
    context = RuleContext(
        description="COFFEE SHOP",
        merchant_key="COFFEE SHOP",
        amount_cents=-500,
        transaction_date=date(2026, 8, 15),
        direction="expense",
        account_id=None,
        provider_key=None,
    )

    simulation = simulate_rules(session, workspace.id, context)

    assert [match.rule.id for match in simulation.matches] == [first.id, second.id]
    assert simulation.winner is not None
    assert simulation.winner.rule.id == first.id
    assert simulation.decision.category_id == first_category.id
    assert simulation.decision.merchant_rule_id == first.id


def test_simulator_uses_builtin_fallback_when_no_workspace_rule_matches(
    session: Session, workspace: Workspace
) -> None:
    """Break if read-only simulation stops before established provider/built-in precedence."""
    categories = {
        name: Category(workspace_id=None, name=name, name_key=name.casefold(), kind=kind)
        for name, kind in BUILTIN_CATEGORY_DEFINITIONS
    }
    session.add_all(categories.values())
    session.commit()
    context = RuleContext(
        description="Netflix.com",
        merchant_key="NETFLIX COM",
        amount_cents=-1_599,
        transaction_date=date(2026, 8, 15),
        direction="expense",
        account_id=None,
        provider_key=None,
    )

    simulation = simulate_rules(session, workspace.id, context)

    assert simulation.matches == ()
    assert simulation.winner is None
    assert simulation.decision.source is CategorizationSource.BUILTIN_RULE
    assert simulation.decision.category_id == categories["Entertainment"].id
