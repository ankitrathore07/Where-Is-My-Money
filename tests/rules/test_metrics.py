from datetime import UTC, date, datetime

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session

from app.categorization.types import CategorizationSource
from app.db.models import (
    Category,
    MerchantRule,
    Transaction,
    TransactionCategorizationEvent,
    Workspace,
)
from app.rules.metrics import build_rule_metrics


def _rule(session: Session, workspace_id: int, category_id: int, name: str) -> MerchantRule:
    rule = MerchantRule(
        workspace_id=workspace_id,
        name=name,
        enabled=True,
        priority=0 if name == "First" else 1,
        condition_version=1,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": "contains",
            "value": "MATCH",
        },
        lock_version=1,
        normalized_merchant="Matched",
        category_id=category_id,
    )
    session.add(rule)
    session.flush()
    return rule


def test_rule_metrics_use_90_day_window_real_events_and_full_rule_order(
    session: Session,
    workspace: Workspace,
) -> None:
    category = Category(
        workspace_id=workspace.id,
        name="Metrics",
        name_key="metrics",
        kind="expense",
    )
    session.add(category)
    session.flush()
    first = _rule(session, workspace.id, category.id, "First")
    second = _rule(session, workspace.id, category.id, "Second")

    sources = (
        [CategorizationSource.WORKSPACE_RULE] * 10
        + [CategorizationSource.PROVIDER_RULE] * 10
        + [CategorizationSource.AI_SUGGESTION] * 5
        + [CategorizationSource.MANUAL] * 10
        + [CategorizationSource.UNCATEGORIZED] * 5
    )
    sources[0], sources[25] = sources[25], sources[0]
    transactions: list[Transaction] = []
    for index, source in enumerate(sources):
        transaction = Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 8, 1, tzinfo=UTC),
            description=f"{'MATCH' if index < 8 else 'OTHER'} {index}",
            amount_cents=-100,
            category_id=category.id,
            categorization_source=source.value,
            merchant_rule_id=first.id if source is CategorizationSource.WORKSPACE_RULE else None,
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        session.add(transaction)
        transactions.append(transaction)
    session.flush()
    for transaction in (transactions[0], transactions[25]):
        session.add(
            TransactionCategorizationEvent(
                workspace_id=workspace.id,
                transaction_id=transaction.id,
                previous_source=CategorizationSource.WORKSPACE_RULE.value,
                new_source=CategorizationSource.MANUAL.value,
                previous_rule_id=first.id,
                new_rule_id=None,
                reason="manual_correction",
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
            )
        )
    session.add(
        Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 5, 17, tzinfo=UTC),
            description="MATCH OUTSIDE WINDOW",
            amount_cents=-100,
            categorization_source=CategorizationSource.UNCATEGORIZED.value,
        )
    )
    session.flush()

    statements: list[str] = []
    engine = session.get_bind()

    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        statements.append(statement)

    sqlalchemy_event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        report = build_rule_metrics(session, workspace.id, date(2026, 8, 15))
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture_statement)

    assert report.window_start == date(2026, 5, 18)
    assert report.window_end == date(2026, 8, 15)
    assert report.total_transactions == 40
    assert report.workspace_rule_coverage_basis_points == 2500
    assert report.provider_builtin_coverage_basis_points == 2500
    assert report.ai_coverage_basis_points == 1250
    assert report.uncategorized_rate_basis_points == 1250
    assert report.manual_correction_rate_basis_points == 500
    assert report.conflicting_rule_rate_basis_points == 2000
    by_id = {item.rule_id: item for item in report.rules}
    assert by_id[first.id].linked_transaction_count == 10
    assert by_id[first.id].match_count_90d == 8
    assert by_id[first.id].higher_priority_conflict_count_90d == 0
    assert by_id[first.id].protected_manual_match_count_90d == 1
    assert by_id[first.id].manual_correction_count_90d == 2
    assert by_id[second.id].match_count_90d == 8
    assert by_id[second.id].higher_priority_conflict_count_90d == 8
    assert len(statements) <= 10
    event_queries = [
        statement for statement in statements if "transaction_categorization_events" in statement
    ]
    assert event_queries
    assert all("description" not in statement for statement in event_queries)


def test_empty_workspace_metrics_are_zero_not_division_errors(
    session: Session,
    workspace: Workspace,
) -> None:
    report = build_rule_metrics(session, workspace.id, date(2026, 8, 15))

    assert report.total_transactions == 0
    assert report.uncategorized_rate_basis_points == 0
    assert report.manual_correction_rate_basis_points == 0
    assert report.conflicting_rule_rate_basis_points == 0


def test_provider_dependent_order_metrics_are_marked_unavailable(
    session: Session,
    workspace: Workspace,
) -> None:
    category = Category(
        workspace_id=workspace.id,
        name="Provider metrics",
        name_key="provider metrics",
        kind="expense",
    )
    session.add(category)
    session.flush()
    provider_rule = MerchantRule(
        workspace_id=workspace.id,
        name="Provider first",
        enabled=True,
        priority=0,
        condition_version=1,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "provider_key",
            "operator": "equal",
            "value": "chase_bank_csv",
        },
        lock_version=1,
        category_id=category.id,
    )
    lower = MerchantRule(
        workspace_id=workspace.id,
        name="Description second",
        enabled=True,
        priority=1,
        condition_version=1,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "description",
            "operator": "contains",
            "value": "MATCH",
        },
        lock_version=1,
        category_id=category.id,
    )
    session.add_all([provider_rule, lower])
    session.add(
        Transaction(
            workspace_id=workspace.id,
            date=datetime(2026, 8, 1, tzinfo=UTC),
            description="MATCH",
            amount_cents=-100,
            categorization_source=CategorizationSource.MANUAL.value,
        )
    )
    session.flush()

    report = build_rule_metrics(session, workspace.id, date(2026, 8, 15))
    by_id = {item.rule_id: item for item in report.rules}

    assert "provider_provenance_unavailable" in report.limitation_codes
    assert report.conflicting_rule_rate_basis_points is None
    assert by_id[provider_rule.id].match_count_90d is None
    assert by_id[provider_rule.id].protected_manual_match_count_90d is None
    assert by_id[lower.id].match_count_90d == 1
    assert by_id[lower.id].higher_priority_conflict_count_90d is None


def test_correction_rate_uses_the_same_transaction_date_cohort(
    session: Session,
    workspace: Workspace,
) -> None:
    recent = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, 1, tzinfo=UTC),
        description="RECENT",
        amount_cents=-100,
        categorization_source=CategorizationSource.UNCATEGORIZED.value,
    )
    old = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 5, 17, tzinfo=UTC),
        description="OLD",
        amount_cents=-100,
        categorization_source=CategorizationSource.MANUAL.value,
    )
    session.add_all([recent, old])
    session.flush()
    session.add(
        TransactionCategorizationEvent(
            workspace_id=workspace.id,
            transaction_id=old.id,
            previous_source=CategorizationSource.WORKSPACE_RULE.value,
            new_source=CategorizationSource.MANUAL.value,
            previous_rule_id=None,
            new_rule_id=None,
            reason="manual_correction",
            created_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
    )
    session.flush()

    report = build_rule_metrics(session, workspace.id, date(2026, 8, 15))

    assert report.total_transactions == 1
    assert report.manual_correction_rate_basis_points == 0
