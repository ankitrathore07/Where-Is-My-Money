"""Bounded, privacy-safe quality metrics for workspace categorization rules."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.categorization.normalization import merchant_key
from app.categorization.types import CategorizationSource
from app.db.models import (
    Account,
    ImportJob,
    MerchantRule,
    Transaction,
    TransactionCategorizationEvent,
)
from app.rules.evaluation import evaluate_condition
from app.rules.loader import load_compiled_rule_set
from app.rules.types import RuleContext


@dataclass(frozen=True)
class RuleMetric:
    rule_id: int
    linked_transaction_count: int
    last_committed_use: datetime | None
    match_count_90d: int
    higher_priority_conflict_count_90d: int
    protected_manual_match_count_90d: int
    manual_correction_count_90d: int
    manual_correction_rate_basis_points: int


@dataclass(frozen=True)
class RuleMetricsReport:
    window_start: date
    window_end: date
    total_transactions: int
    workspace_rule_coverage_basis_points: int
    provider_builtin_coverage_basis_points: int
    ai_coverage_basis_points: int
    uncategorized_rate_basis_points: int
    manual_correction_rate_basis_points: int
    conflicting_rule_rate_basis_points: int
    rules: tuple[RuleMetric, ...]


def _basis_points(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return (numerator * 10_000 + denominator // 2) // denominator


def build_rule_metrics(
    session: Session,
    workspace_id: int,
    as_of_date: date,
) -> RuleMetricsReport:
    """Build one 90-day report with a constant number of projected queries."""
    window_start = as_of_date - timedelta(days=89)
    start_at = datetime.combine(window_start, time.min, tzinfo=UTC)
    end_at = datetime.combine(as_of_date + timedelta(days=1), time.min, tzinfo=UTC)
    rule_ids = tuple(
        session.scalars(
            select(MerchantRule.id)
            .where(MerchantRule.workspace_id == workspace_id)
            .order_by(MerchantRule.priority, MerchantRule.id)
        )
    )
    compiled = load_compiled_rule_set(session, workspace_id)
    rows = tuple(
        session.execute(
            select(
                Transaction.id,
                Transaction.date,
                Transaction.description,
                Transaction.amount_cents,
                Transaction.categorization_source,
                Transaction.merchant_rule_id,
                Account.id.label("account_id"),
            )
            .select_from(Transaction)
            .outerjoin(
                ImportJob,
                and_(
                    ImportJob.id == Transaction.import_job_id,
                    ImportJob.workspace_id == workspace_id,
                ),
            )
            .outerjoin(
                Account,
                and_(
                    Account.id == ImportJob.account_id,
                    Account.workspace_id == workspace_id,
                ),
            )
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.date >= start_at,
                Transaction.date < end_at,
            )
            .order_by(Transaction.id)
        ).mappings()
    )

    source_counts: Counter[str] = Counter()
    match_counts: Counter[int] = Counter()
    conflict_counts: Counter[int] = Counter()
    protected_manual_counts: Counter[int] = Counter()
    linked_counts_90d: Counter[int] = Counter()
    conflicting_transactions = 0
    for row in rows:
        source = str(row["categorization_source"])
        source_counts[source] += 1
        if row["merchant_rule_id"] is not None:
            linked_counts_90d[int(row["merchant_rule_id"])] += 1
        transaction_date = row["date"]
        assert isinstance(transaction_date, datetime)
        amount_cents = int(row["amount_cents"])
        context = RuleContext(
            description=str(row["description"]),
            merchant_key=merchant_key(str(row["description"])),
            amount_cents=amount_cents,
            transaction_date=transaction_date.date(),
            direction="income" if amount_cents > 0 else "expense" if amount_cents < 0 else "zero",
            account_id=int(row["account_id"]) if row["account_id"] is not None else None,
            provider_key=None,
        )
        matching_rule_ids = tuple(
            rule.id
            for rule in compiled.rules
            if evaluate_condition(rule.condition, context).matched
        )
        if len(matching_rule_ids) > 1:
            conflicting_transactions += 1
        for index, rule_id in enumerate(matching_rule_ids):
            match_counts[rule_id] += 1
            if index > 0:
                conflict_counts[rule_id] += 1
            if source == CategorizationSource.MANUAL.value:
                protected_manual_counts[rule_id] += 1

    linked_usage = {
        int(rule_id): (int(count), last_use)
        for rule_id, count, last_use in session.execute(
            select(
                Transaction.merchant_rule_id,
                func.count(Transaction.id),
                func.max(Transaction.created_at),
            )
            .where(
                Transaction.workspace_id == workspace_id,
                Transaction.merchant_rule_id.is_not(None),
            )
            .group_by(Transaction.merchant_rule_id)
        )
    }
    event_last_use = {
        int(rule_id): last_use
        for rule_id, last_use in session.execute(
            select(
                TransactionCategorizationEvent.new_rule_id,
                func.max(TransactionCategorizationEvent.created_at),
            )
            .where(
                TransactionCategorizationEvent.workspace_id == workspace_id,
                TransactionCategorizationEvent.new_rule_id.is_not(None),
            )
            .group_by(TransactionCategorizationEvent.new_rule_id)
        )
    }
    correction_rows = tuple(
        session.execute(
            select(
                TransactionCategorizationEvent.transaction_id,
                TransactionCategorizationEvent.previous_rule_id,
            ).where(
                TransactionCategorizationEvent.workspace_id == workspace_id,
                TransactionCategorizationEvent.reason == "manual_correction",
                TransactionCategorizationEvent.created_at >= start_at,
                TransactionCategorizationEvent.created_at < end_at,
            )
        )
    )
    corrected_transaction_ids = {
        int(transaction_id) for transaction_id, _rule_id in correction_rows
    }
    correction_counts: Counter[int] = Counter(
        int(rule_id) for _transaction_id, rule_id in correction_rows if rule_id is not None
    )

    metrics = []
    for rule_id in rule_ids:
        linked_count, transaction_last_use = linked_usage.get(rule_id, (0, None))
        last_committed_use = max(
            (value for value in (transaction_last_use, event_last_use.get(rule_id)) if value),
            default=None,
        )
        correction_count = correction_counts[rule_id]
        metrics.append(
            RuleMetric(
                rule_id=rule_id,
                linked_transaction_count=linked_count,
                last_committed_use=last_committed_use,
                match_count_90d=match_counts[rule_id],
                higher_priority_conflict_count_90d=conflict_counts[rule_id],
                protected_manual_match_count_90d=protected_manual_counts[rule_id],
                manual_correction_count_90d=correction_count,
                manual_correction_rate_basis_points=_basis_points(
                    correction_count,
                    linked_counts_90d[rule_id] + correction_count,
                ),
            )
        )

    total = len(rows)
    provider_builtin = (
        source_counts[CategorizationSource.PROVIDER_RULE.value]
        + source_counts[CategorizationSource.BUILTIN_RULE.value]
    )
    return RuleMetricsReport(
        window_start=window_start,
        window_end=as_of_date,
        total_transactions=total,
        workspace_rule_coverage_basis_points=_basis_points(
            source_counts[CategorizationSource.WORKSPACE_RULE.value], total
        ),
        provider_builtin_coverage_basis_points=_basis_points(provider_builtin, total),
        ai_coverage_basis_points=_basis_points(
            source_counts[CategorizationSource.AI_SUGGESTION.value], total
        ),
        uncategorized_rate_basis_points=_basis_points(
            source_counts[CategorizationSource.UNCATEGORIZED.value], total
        ),
        manual_correction_rate_basis_points=_basis_points(len(corrected_transaction_ids), total),
        conflicting_rule_rate_basis_points=_basis_points(conflicting_transactions, total),
        rules=tuple(metrics),
    )
