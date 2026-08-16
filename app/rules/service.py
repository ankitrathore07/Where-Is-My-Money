"""Workspace-scoped lifecycle management for typed merchant rules."""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import and_, case, delete, insert, or_, select, update
from sqlalchemy.orm import Session

from app.categorization.normalization import merchant_display_fallback, merchant_key
from app.categorization.sanitization import sanitize_transaction_description
from app.categorization.service import categorize_candidate
from app.categorization.types import CategorizationDecision, CategorizationSource
from app.db.models import (
    Account,
    Category,
    ImportJob,
    MerchantRule,
    RuleApplicationRun,
    Tag,
    Transaction,
    Workspace,
    WorkspaceMembership,
    transaction_tags,
)
from app.imports.types import NormalizedTransaction
from app.rules.application_tokens import (
    MAX_SELECTED_TRANSACTION_IDS,
    ApplicationTokenPayload,
    canonical_application_selection,
    create_application_token,
    load_application_token,
)
from app.rules.evaluation import (
    CompiledWorkspaceRule,
    CompiledWorkspaceRuleSet,
    WorkspaceRuleMatch,
    evaluate_condition,
)
from app.rules.loader import load_compiled_rule_set
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
    RuleContext,
)
from app.rules.validation import RuleConditionValidationError, condition_to_json, parse_condition
from app.tags.service import tag_ids_with_subscription

MAX_RULE_NAME_LENGTH = 120
MAX_MERCHANT_NAME_LENGTH = 255
MAX_PREVIEW_EXAMPLES = 20
HISTORY_PAGE_SIZE = 200
PREVIEW_LIMITATION_PROVIDER_UNAVAILABLE = "historical_provider_unavailable"


class RuleNotFoundError(LookupError):
    """Raised when a rule is absent from the active workspace."""


class RuleResourceNotFoundError(LookupError):
    """Raised when a draft references a resource outside the active workspace."""


class RuleConflictError(RuntimeError):
    """Raised when an optimistic-lock version is no longer current."""


class StaleRuleApplicationError(RuleConflictError):
    """Raised when signed historical preview state is no longer current."""


class RuleValidationError(ValueError):
    """Raised with field-specific errors for an invalid lifecycle request."""

    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the rule details below.")
        self.field_errors = field_errors


@dataclass(frozen=True)
class RuleDraft:
    """Validated-on-use typed condition and action values for a workspace rule."""

    name: str
    condition: ConditionNode
    normalized_merchant: str | None
    category_id: int
    tag_ids: tuple[int, ...] = ()
    is_subscription: bool = False
    billing_period_months: int | None = None


@dataclass(frozen=True)
class RuleSimulation:
    """Every workspace match plus the established winning categorization decision."""

    matches: tuple[WorkspaceRuleMatch, ...]
    decision: CategorizationDecision

    @property
    def winner(self) -> WorkspaceRuleMatch | None:
        return self.matches[0] if self.matches else None


@dataclass(frozen=True)
class PreviewGroupCount:
    """An aggregate for one authorized category or account."""

    group_id: int | None
    label: str
    count: int


@dataclass(frozen=True)
class PreviewConflict:
    """A higher-priority winner that shadows the draft for one or more rows."""

    winning_rule_id: int
    winning_rule_name: str
    count: int


@dataclass(frozen=True)
class PreviewExample:
    """One authorized, sanitized example with no amount or source payload."""

    transaction_id: int
    description: str
    outcome: str
    winning_rule_id: int | None = None


@dataclass(frozen=True)
class RuleImpactPreview:
    """Exact aggregate impact with a bounded, privacy-safe example collection."""

    matched_count: int
    would_change_count: int
    unchanged_count: int
    manual_skip_count: int
    conflict_skip_count: int
    not_matched_count: int
    unavailable_count: int
    limitation_codes: tuple[str, ...]
    category_counts: tuple[PreviewGroupCount, ...]
    account_counts: tuple[PreviewGroupCount, ...]
    conflicts: tuple[PreviewConflict, ...]
    examples: tuple[PreviewExample, ...]


@dataclass(frozen=True)
class RuleDeletionPreview:
    """Historical contexts whose current winning rule would change after deletion."""

    target_winner_count: int
    next_rule_match_count: int
    fallback_match_count: int
    unavailable_count: int


@dataclass(frozen=True)
class HistoryFilters:
    """Allowlisted filters for one bounded historical application preview."""

    date_from: date | str | None = None
    date_to: date | str | None = None
    account_id: int | None = None
    category_id: int | None = None
    direction: str | None = "all"


@dataclass(frozen=True)
class HistoricalApplicationPreview:
    """Redacted counts, bounded selectable IDs, and signed confirmation state."""

    run_id: int
    token: str
    state_digest: str
    rule_id: int
    rule_name: str
    normalized_merchant: str | None
    category_id: int | None
    tag_ids: tuple[int, ...]
    is_subscription: bool
    billing_period_months: int | None
    normalized_filters: Mapping[str, object]
    matched_count: int
    would_change_count: int
    unchanged_count: int
    manual_skip_count: int
    conflict_skip_count: int
    not_matched_count: int
    unavailable_count: int
    invalid_count: int
    eligible_transaction_ids: tuple[int, ...]
    selected_transaction_ids: tuple[int, ...]


@dataclass(frozen=True)
class HistoricalApplicationResult:
    """Stable confirmed audit outcome returned by first use and every retry."""

    run_id: int
    matched_count: int
    changed_count: int
    unchanged_count: int
    manual_skip_count: int
    conflict_skip_count: int


@dataclass(frozen=True)
class _PreviewTransaction:
    id: int
    transaction_date: datetime
    description: str
    normalized_merchant: str | None
    amount_cents: int
    category_id: int | None
    category_label: str
    categorization_source: str
    is_subscription: bool
    billing_period_months: int | None
    account_id: int | None
    account_label: str
    tag_ids: tuple[int, ...]


@dataclass(frozen=True)
class _ValidatedDraft:
    name: str
    condition: ConditionNode
    condition_json: dict[str, object]
    normalized_merchant: str | None
    category_id: int
    tags: tuple[Tag, ...]
    is_subscription: bool
    billing_period_months: int | None


@dataclass(frozen=True)
class _HistoryTransaction:
    id: int
    transaction_date: datetime
    description: str
    normalized_merchant: str | None
    amount_cents: int
    category_id: int | None
    categorization_source: str
    is_subscription: bool
    billing_period_months: int | None
    merchant_rule_id: int | None
    account_id: int | None
    tag_ids: tuple[int, ...]


@dataclass(frozen=True)
class _CategorizationState:
    normalized_merchant: str | None
    category_id: int | None
    tag_ids: tuple[int, ...]
    is_subscription: bool
    billing_period_months: int | None
    categorization_source: str
    merchant_rule_id: int | None


@dataclass(frozen=True)
class _SelectedHistoryState:
    transaction_id: int
    current: _CategorizationState
    resulting: _CategorizationState


@dataclass(frozen=True)
class _HistoryEvaluation:
    matched_count: int
    would_change_count: int
    unchanged_count: int
    manual_skip_count: int
    conflict_skip_count: int
    not_matched_count: int
    unavailable_count: int
    invalid_count: int
    eligible_transaction_ids: tuple[int, ...]
    selected_states: tuple[_SelectedHistoryState, ...]


@dataclass(frozen=True)
class _HistoricalApplicationRule:
    id: int
    condition: ConditionNode | None
    compiled: CompiledWorkspaceRule | None
    invalid_action: bool


def preview_historical_application(
    session: Session,
    workspace_id: int,
    rule_id: int,
    filters: HistoryFilters,
    *,
    selected_transaction_ids: tuple[int, ...] | list[int] | None = None,
    user_id: int | None = None,
    secret_key: str | None = None,
) -> HistoricalApplicationPreview:
    """Create a redacted audit preview and signed, bounded confirmation token."""
    rule = get_rule(session, workspace_id, rule_id)
    normalized_filters = _normalize_history_filters(session, workspace_id, filters)
    requested_ids = _normalize_selected_transaction_ids(selected_transaction_ids)
    initiated_by_user_id = _application_user_id(session, workspace_id, user_id)
    rules, target = _historical_rule_plan(session, workspace_id, rule_id)
    evaluation = _evaluate_historical_application(
        session,
        workspace_id,
        rules,
        target,
        normalized_filters,
        requested_ids=requested_ids,
    )
    selected_states = evaluation.selected_states
    selected_ids = tuple(item.transaction_id for item in selected_states)
    state_digest = _historical_state_digest(
        workspace_id,
        rule_id,
        rule.lock_version,
        normalized_filters,
        selected_states,
    )
    payload = ApplicationTokenPayload(
        workspace_id=workspace_id,
        merchant_rule_id=rule_id,
        rule_lock_version=rule.lock_version,
        selected_transaction_ids=selected_ids,
        state_digest=state_digest,
        normalized_filters=normalized_filters,
    )
    token = create_application_token(_application_secret(secret_key), payload)
    run = RuleApplicationRun(
        workspace_id=workspace_id,
        merchant_rule_id=rule_id,
        initiated_by_user_id=initiated_by_user_id,
        rule_name_snapshot=rule.name,
        rule_lock_version=rule.lock_version,
        status="previewed",
        selection_json=canonical_application_selection(payload),
        preview_digest=state_digest,
        matched_count=evaluation.matched_count,
        changed_count=len(selected_ids),
        unchanged_count=evaluation.unchanged_count,
        manual_skip_count=evaluation.manual_skip_count,
        conflict_skip_count=evaluation.conflict_skip_count,
    )
    session.add(run)
    session.flush()
    return HistoricalApplicationPreview(
        run_id=run.id,
        token=token,
        state_digest=state_digest,
        rule_id=rule.id,
        rule_name=rule.name,
        normalized_merchant=(
            target.compiled.normalized_merchant if target.compiled is not None else None
        ),
        category_id=(target.compiled.category_id if target.compiled is not None else None),
        tag_ids=(tuple(sorted(target.compiled.tag_ids)) if target.compiled is not None else ()),
        is_subscription=(target.compiled.is_subscription if target.compiled is not None else False),
        billing_period_months=(
            target.compiled.billing_period_months if target.compiled is not None else None
        ),
        normalized_filters=dict(normalized_filters),
        matched_count=evaluation.matched_count,
        would_change_count=evaluation.would_change_count,
        unchanged_count=evaluation.unchanged_count,
        manual_skip_count=evaluation.manual_skip_count,
        conflict_skip_count=evaluation.conflict_skip_count,
        not_matched_count=evaluation.not_matched_count,
        unavailable_count=evaluation.unavailable_count,
        invalid_count=evaluation.invalid_count,
        eligible_transaction_ids=evaluation.eligible_transaction_ids,
        selected_transaction_ids=selected_ids,
    )


def confirm_historical_application(
    session: Session,
    workspace_id: int,
    token: str,
    user_id: int,
    *,
    secret_key: str | None = None,
) -> HistoricalApplicationResult:
    """Recompute signed state under locks and atomically apply one bounded selection."""
    payload = load_application_token(_application_secret(secret_key), token)
    if payload.workspace_id != workspace_id:
        raise StaleRuleApplicationError("The application preview changed; reload and try again.")
    _application_user_id(session, workspace_id, user_id)
    _serialize_workspace_ordering(session, workspace_id)

    selection = canonical_application_selection(payload)
    confirmed = _confirmed_application_run(
        session,
        workspace_id,
        payload.state_digest,
        selection,
        payload.rule_lock_version,
    )
    if confirmed is not None:
        return _historical_application_result(confirmed)

    preview_run = _preview_application_run(
        session,
        workspace_id,
        payload.state_digest,
        selection,
    )
    if preview_run is None:
        raise StaleRuleApplicationError("The application preview changed; reload and try again.")

    try:
        rule = _locked_rule(session, workspace_id, payload.merchant_rule_id)
        if rule.lock_version != payload.rule_lock_version:
            _mark_application_stale(session, preview_run)
        normalized_filters = _normalize_history_filters(
            session,
            workspace_id,
            _history_filters_from_mapping(payload.normalized_filters),
        )
        if normalized_filters != dict(payload.normalized_filters):
            _mark_application_stale(session, preview_run)
        rules, target = _historical_rule_plan(
            session,
            workspace_id,
            payload.merchant_rule_id,
        )
        if target.compiled is None:
            _mark_application_stale(session, preview_run)
        evaluation = _evaluate_historical_application(
            session,
            workspace_id,
            rules,
            target,
            normalized_filters,
            requested_ids=payload.selected_transaction_ids,
            restrict_ids=payload.selected_transaction_ids,
            lock_rows=True,
        )
    except (RuleNotFoundError, RuleResourceNotFoundError, RuleValidationError):
        _mark_application_stale(session, preview_run)

    recomputed_digest = _historical_state_digest(
        workspace_id,
        payload.merchant_rule_id,
        rule.lock_version,
        normalized_filters,
        evaluation.selected_states,
    )
    if recomputed_digest != payload.state_digest:
        _mark_application_stale(session, preview_run)

    with session.begin_nested():
        assert target.compiled is not None
        _apply_historical_states(
            session,
            workspace_id,
            target.compiled,
            evaluation.selected_states,
        )
        preview_run.status = "confirmed"
        preview_run.changed_count = len(evaluation.selected_states)
        preview_run.confirmed_at = datetime.now(UTC)
        session.flush()
    return _historical_application_result(preview_run)


def _normalize_history_filters(
    session: Session,
    workspace_id: int,
    filters: HistoryFilters,
) -> dict[str, object]:
    if type(filters) is not HistoryFilters:
        raise RuleValidationError({"filters": "Choose valid history filters."})
    errors: dict[str, str] = {}
    date_from = _normalize_history_date(filters.date_from, "date_from", errors)
    date_to = _normalize_history_date(filters.date_to, "date_to", errors)
    account_id = _normalize_history_resource_id(filters.account_id, "account_id", errors)
    category_id = _normalize_history_resource_id(filters.category_id, "category_id", errors)
    if filters.direction is None:
        direction = "all"
    elif isinstance(filters.direction, str):
        direction = filters.direction.strip().casefold() or "all"
    else:
        direction = ""
        errors["direction"] = "Choose a valid transaction direction."
    if direction not in {"all", "expense", "income", "zero"}:
        errors["direction"] = "Choose a valid transaction direction."
    if date_from is not None and date_to is not None and date_from > date_to:
        errors["date_to"] = "End date must be on or after start date."
    if errors:
        raise RuleValidationError(errors)

    if (
        account_id is not None
        and session.scalar(
            select(Account.id).where(
                Account.id == account_id,
                Account.workspace_id == workspace_id,
            )
        )
        is None
    ):
        raise RuleResourceNotFoundError("Rule resource not found.")
    if (
        category_id is not None
        and session.scalar(
            select(Category.id).where(
                Category.id == category_id,
                or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
            )
        )
        is None
    ):
        raise RuleResourceNotFoundError("Rule resource not found.")
    return {
        "account_id": account_id,
        "category_id": category_id,
        "date_from": date_from,
        "date_to": date_to,
        "direction": direction,
    }


def _normalize_history_date(
    value: date | str | None,
    field: str,
    errors: dict[str, str],
) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        errors[field] = "Choose a valid date."
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return date.fromisoformat(normalized).isoformat()
        except ValueError:
            pass
    errors[field] = "Choose a valid date."
    return None


def _normalize_history_resource_id(
    value: int | None,
    field: str,
    errors: dict[str, str],
) -> int | None:
    if value is None:
        return None
    if type(value) is int and value > 0:
        return value
    errors[field] = "Choose a valid resource."
    return None


def _normalize_selected_transaction_ids(
    selected_ids: tuple[int, ...] | list[int] | None,
) -> tuple[int, ...] | None:
    if selected_ids is None:
        return None
    valid = (
        type(selected_ids) in {tuple, list}
        and len(selected_ids) <= MAX_SELECTED_TRANSACTION_IDS
        and all(
            type(transaction_id) is int and transaction_id > 0 for transaction_id in selected_ids
        )
        and len(selected_ids) == len(set(selected_ids))
    )
    if not valid:
        raise RuleValidationError(
            {"selected_transaction_ids": "Choose at most 500 valid transactions."}
        )
    return tuple(sorted(selected_ids))


def _application_user_id(
    session: Session,
    workspace_id: int,
    user_id: int | None,
) -> int:
    workspace = session.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if workspace is None:
        raise RuleNotFoundError("Rule not found.")
    selected_user_id = workspace.owner_id if user_id is None else user_id
    if type(selected_user_id) is not int or selected_user_id <= 0:
        raise RuleNotFoundError("Rule not found.")
    authorized = session.scalar(
        select(WorkspaceMembership.id).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == selected_user_id,
        )
    )
    if authorized is None:
        raise RuleNotFoundError("Rule not found.")
    return selected_user_id


def _application_secret(secret_key: str | None) -> str:
    if secret_key is None:
        from app.core.config import settings

        return settings.secret_key or ""
    return secret_key


def _historical_rule_plan(
    session: Session,
    workspace_id: int,
    rule_id: int,
) -> tuple[tuple[_HistoricalApplicationRule, ...], _HistoricalApplicationRule]:
    compiled = load_compiled_rule_set(session, workspace_id)
    compiled_by_id = {rule.id: rule for rule in compiled.rules}
    diagnostics_by_id = {item.rule_id: item.reason for item in compiled.diagnostics}
    planned: list[_HistoricalApplicationRule] = []
    for raw_rule in list_rules(session, workspace_id):
        if not raw_rule.enabled:
            continue
        compiled_rule = compiled_by_id.get(raw_rule.id)
        diagnostic = diagnostics_by_id.get(raw_rule.id)
        condition: ConditionNode | None
        if compiled_rule is not None:
            condition = compiled_rule.condition
        elif diagnostic == "invalid_condition":
            condition = None
        else:
            try:
                condition = parse_condition(_persisted_condition_payload(raw_rule))
            except RuleConditionValidationError:
                condition = None
        planned.append(
            _HistoricalApplicationRule(
                id=raw_rule.id,
                condition=condition,
                compiled=compiled_rule,
                invalid_action=compiled_rule is None and condition is not None,
            )
        )
    rules = tuple(planned)
    target = next((rule for rule in rules if rule.id == rule_id), None)
    if target is None:
        raise RuleResourceNotFoundError("Rule resource not found.")
    if target.condition is None:
        raise RuleValidationError({"condition": "Choose a valid rule condition."})
    return rules, target


def _evaluate_historical_application(
    session: Session,
    workspace_id: int,
    rules: tuple[_HistoricalApplicationRule, ...],
    target: _HistoricalApplicationRule,
    normalized_filters: Mapping[str, object],
    *,
    requested_ids: tuple[int, ...] | None,
    restrict_ids: tuple[int, ...] | None = None,
    lock_rows: bool = False,
) -> _HistoryEvaluation:
    requested_set = set(requested_ids) if requested_ids is not None else None
    selected: dict[int, _SelectedHistoryState] = {}
    eligible_ids: list[int] = []
    matched_count = 0
    would_change_count = 0
    unchanged_count = 0
    manual_skip_count = 0
    conflict_skip_count = 0
    not_matched_count = 0
    unavailable_count = 0
    invalid_count = 0

    for transaction in _history_transactions(
        session,
        workspace_id,
        normalized_filters,
        restrict_ids=restrict_ids,
        lock_rows=lock_rows,
    ):
        context = _history_rule_context(transaction)
        assert target.condition is not None
        target_outcome = _evaluate_historical_condition(target.condition, context)
        if target_outcome is None:
            unavailable_count += 1
            continue
        if not target_outcome:
            not_matched_count += 1
            continue
        matched_count += 1
        if transaction.categorization_source == CategorizationSource.MANUAL.value:
            manual_skip_count += 1
            continue

        winner, ambiguous = _historical_application_winner(rules, target.id, context)
        if ambiguous or winner is None:
            unavailable_count += 1
            continue
        if winner.invalid_action:
            invalid_count += 1
            continue
        if winner.id != target.id:
            conflict_skip_count += 1
            continue
        assert target.compiled is not None

        current = _current_categorization_state(transaction)
        resulting = _resulting_categorization_state(transaction, target.compiled)
        if current == resulting:
            unchanged_count += 1
            continue

        would_change_count += 1
        snapshot = _SelectedHistoryState(transaction.id, current, resulting)
        if len(eligible_ids) < MAX_SELECTED_TRANSACTION_IDS:
            eligible_ids.append(transaction.id)
        if requested_set is None:
            if len(selected) < MAX_SELECTED_TRANSACTION_IDS:
                selected[transaction.id] = snapshot
        elif transaction.id in requested_set:
            selected[transaction.id] = snapshot

    if requested_ids is not None and set(selected) != set(requested_ids):
        raise RuleNotFoundError("Transaction not found.")
    ordered_selected = tuple(selected[transaction_id] for transaction_id in sorted(selected))
    return _HistoryEvaluation(
        matched_count=matched_count,
        would_change_count=would_change_count,
        unchanged_count=unchanged_count,
        manual_skip_count=manual_skip_count,
        conflict_skip_count=conflict_skip_count,
        not_matched_count=not_matched_count,
        unavailable_count=unavailable_count,
        invalid_count=invalid_count,
        eligible_transaction_ids=tuple(eligible_ids),
        selected_states=ordered_selected,
    )


def _historical_application_winner(
    rules: tuple[_HistoricalApplicationRule, ...],
    target_id: int,
    context: RuleContext,
) -> tuple[_HistoricalApplicationRule | None, bool]:
    unknown_before_winner = False
    for rule in rules:
        if rule.condition is not None:
            outcome = _evaluate_historical_condition(rule.condition, context)
            if outcome is None:
                unknown_before_winner = True
            elif outcome:
                return (None, True) if unknown_before_winner else (rule, False)
        if rule.id == target_id:
            break
    return None, unknown_before_winner


def _history_transactions(
    session: Session,
    workspace_id: int,
    normalized_filters: Mapping[str, object],
    *,
    restrict_ids: tuple[int, ...] | None,
    lock_rows: bool,
) -> Iterator[_HistoryTransaction]:
    last_id = 0
    while True:
        id_statement = (
            select(Transaction.id)
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
            .where(Transaction.workspace_id == workspace_id, Transaction.id > last_id)
            .order_by(Transaction.id)
            .limit(HISTORY_PAGE_SIZE)
        )
        id_statement = _apply_history_filters(id_statement, normalized_filters)
        if restrict_ids is not None:
            if not restrict_ids:
                return
            id_statement = id_statement.where(Transaction.id.in_(restrict_ids))
        if lock_rows:
            id_statement = id_statement.with_for_update(of=Transaction)
        page_ids = tuple(session.scalars(id_statement))
        if not page_ids:
            return

        projection = (
            select(
                Transaction.id,
                Transaction.date,
                Transaction.description,
                Transaction.normalized_merchant,
                Transaction.amount_cents,
                Transaction.category_id,
                Transaction.categorization_source,
                Transaction.is_subscription,
                Transaction.billing_period_months,
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
                Transaction.id.in_(page_ids),
            )
            .order_by(Transaction.id)
        )
        if lock_rows:
            projection = projection.with_for_update(of=Transaction)
        rows = tuple(session.execute(projection).mappings())
        tags_by_transaction: dict[int, list[int]] = {
            transaction_id: [] for transaction_id in page_ids
        }
        for transaction_id, tag_id in session.execute(
            select(transaction_tags.c.transaction_id, transaction_tags.c.tag_id)
            .where(transaction_tags.c.transaction_id.in_(page_ids))
            .order_by(transaction_tags.c.transaction_id, transaction_tags.c.tag_id)
        ):
            tags_by_transaction[int(transaction_id)].append(int(tag_id))
        for row in rows:
            transaction_date = row["date"]
            assert isinstance(transaction_date, datetime)
            transaction_id = int(row["id"])
            yield _HistoryTransaction(
                id=transaction_id,
                transaction_date=transaction_date,
                description=str(row["description"]),
                normalized_merchant=(
                    str(row["normalized_merchant"])
                    if row["normalized_merchant"] is not None
                    else None
                ),
                amount_cents=int(row["amount_cents"]),
                category_id=(int(row["category_id"]) if row["category_id"] is not None else None),
                categorization_source=str(row["categorization_source"]),
                is_subscription=bool(row["is_subscription"]),
                billing_period_months=(
                    int(row["billing_period_months"])
                    if row["billing_period_months"] is not None
                    else None
                ),
                merchant_rule_id=(
                    int(row["merchant_rule_id"]) if row["merchant_rule_id"] is not None else None
                ),
                account_id=(int(row["account_id"]) if row["account_id"] is not None else None),
                tag_ids=tuple(tags_by_transaction[transaction_id]),
            )
        last_id = page_ids[-1]


def _apply_history_filters(statement, filters: Mapping[str, object]):
    date_from = filters["date_from"]
    date_to = filters["date_to"]
    account_id = filters["account_id"]
    category_id = filters["category_id"]
    direction = filters["direction"]
    if isinstance(date_from, str):
        statement = statement.where(
            Transaction.date >= datetime.combine(date.fromisoformat(date_from), time.min, UTC)
        )
    if isinstance(date_to, str):
        statement = statement.where(
            Transaction.date <= datetime.combine(date.fromisoformat(date_to), time.max, UTC)
        )
    if type(account_id) is int:
        statement = statement.where(Account.id == account_id)
    if type(category_id) is int:
        statement = statement.where(Transaction.category_id == category_id)
    if direction == "income":
        statement = statement.where(Transaction.amount_cents > 0)
    elif direction == "expense":
        statement = statement.where(Transaction.amount_cents < 0)
    elif direction == "zero":
        statement = statement.where(Transaction.amount_cents == 0)
    return statement


def _history_rule_context(transaction: _HistoryTransaction) -> RuleContext:
    return RuleContext(
        description=transaction.description,
        merchant_key=merchant_key(transaction.description),
        amount_cents=transaction.amount_cents,
        transaction_date=transaction.transaction_date.date(),
        direction=(
            "income"
            if transaction.amount_cents > 0
            else "expense"
            if transaction.amount_cents < 0
            else "zero"
        ),
        account_id=transaction.account_id,
        provider_key=None,
    )


def _current_categorization_state(transaction: _HistoryTransaction) -> _CategorizationState:
    return _CategorizationState(
        normalized_merchant=transaction.normalized_merchant,
        category_id=transaction.category_id,
        tag_ids=tuple(sorted(transaction.tag_ids)),
        is_subscription=transaction.is_subscription,
        billing_period_months=transaction.billing_period_months,
        categorization_source=transaction.categorization_source,
        merchant_rule_id=transaction.merchant_rule_id,
    )


def _resulting_categorization_state(
    transaction: _HistoryTransaction,
    target: CompiledWorkspaceRule,
) -> _CategorizationState:
    return _CategorizationState(
        normalized_merchant=target.normalized_merchant
        or merchant_display_fallback(transaction.description),
        category_id=target.category_id,
        tag_ids=tuple(sorted(target.tag_ids)),
        is_subscription=target.is_subscription,
        billing_period_months=target.billing_period_months,
        categorization_source=CategorizationSource.WORKSPACE_RULE.value,
        merchant_rule_id=target.id,
    )


def _historical_state_digest(
    workspace_id: int,
    rule_id: int,
    rule_lock_version: int,
    normalized_filters: Mapping[str, object],
    selected_states: tuple[_SelectedHistoryState, ...],
) -> str:
    state = {
        "workspace_id": workspace_id,
        "merchant_rule_id": rule_id,
        "rule_lock_version": rule_lock_version,
        "normalized_filters": dict(sorted(normalized_filters.items())),
        "selected_transaction_ids": [item.transaction_id for item in selected_states],
        "transactions": [
            {
                "id": item.transaction_id,
                "current": _categorization_state_json(item.current),
                "resulting": _categorization_state_json(item.resulting),
            }
            for item in selected_states
        ],
    }
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _categorization_state_json(state: _CategorizationState) -> dict[str, object]:
    return {
        "normalized_merchant": state.normalized_merchant,
        "category_id": state.category_id,
        "tag_ids": list(sorted(state.tag_ids)),
        "is_subscription": state.is_subscription,
        "billing_period_months": state.billing_period_months,
        "categorization_source": state.categorization_source,
        "merchant_rule_id": state.merchant_rule_id,
    }


def _confirmed_application_run(
    session: Session,
    workspace_id: int,
    digest: str,
    selection: Mapping[str, object],
    rule_lock_version: int,
) -> RuleApplicationRun | None:
    runs = session.scalars(
        select(RuleApplicationRun)
        .where(
            RuleApplicationRun.workspace_id == workspace_id,
            RuleApplicationRun.preview_digest == digest,
            RuleApplicationRun.status == "confirmed",
            RuleApplicationRun.rule_lock_version == rule_lock_version,
        )
        .order_by(RuleApplicationRun.id)
        .with_for_update()
    )
    return next((run for run in runs if run.selection_json == selection), None)


def _preview_application_run(
    session: Session,
    workspace_id: int,
    digest: str,
    selection: Mapping[str, object],
) -> RuleApplicationRun | None:
    runs = session.scalars(
        select(RuleApplicationRun)
        .where(
            RuleApplicationRun.workspace_id == workspace_id,
            RuleApplicationRun.preview_digest == digest,
            RuleApplicationRun.status == "previewed",
        )
        .order_by(RuleApplicationRun.id)
        .with_for_update()
    )
    return next((run for run in runs if run.selection_json == selection), None)


def _locked_rule(session: Session, workspace_id: int, rule_id: int) -> MerchantRule:
    rule = session.scalar(
        select(MerchantRule)
        .where(
            MerchantRule.id == rule_id,
            MerchantRule.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if rule is None:
        raise RuleNotFoundError("Rule not found.")
    return rule


def _history_filters_from_mapping(filters: Mapping[str, object]) -> HistoryFilters:
    return HistoryFilters(
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        account_id=filters.get("account_id"),  # type: ignore[arg-type]
        category_id=filters.get("category_id"),  # type: ignore[arg-type]
        direction=filters.get("direction"),  # type: ignore[arg-type]
    )


def _mark_application_stale(session: Session, run: RuleApplicationRun) -> None:
    run.status = "stale"
    run.confirmed_at = None
    session.flush()
    raise StaleRuleApplicationError("The application preview changed; reload and try again.")


def _apply_historical_states(
    session: Session,
    workspace_id: int,
    target: CompiledWorkspaceRule,
    states: tuple[_SelectedHistoryState, ...],
) -> None:
    if not states:
        return
    transaction_ids = tuple(item.transaction_id for item in states)
    normalized_merchants = {
        item.transaction_id: item.resulting.normalized_merchant for item in states
    }
    result = session.execute(
        update(Transaction)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.id.in_(transaction_ids),
        )
        .values(
            normalized_merchant=case(normalized_merchants, value=Transaction.id),
            category_id=target.category_id,
            is_subscription=target.is_subscription,
            billing_period_months=target.billing_period_months,
            categorization_source=CategorizationSource.WORKSPACE_RULE.value,
            merchant_rule_id=target.id,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != len(transaction_ids):
        raise StaleRuleApplicationError("The application preview changed; reload and try again.")
    session.execute(
        delete(transaction_tags).where(transaction_tags.c.transaction_id.in_(transaction_ids))
    )
    tag_ids = tuple(sorted(target.tag_ids))
    if tag_ids:
        session.execute(
            insert(transaction_tags),
            [
                {"transaction_id": transaction_id, "tag_id": tag_id}
                for transaction_id in transaction_ids
                for tag_id in tag_ids
            ],
        )


def _historical_application_result(run: RuleApplicationRun) -> HistoricalApplicationResult:
    return HistoricalApplicationResult(
        run_id=run.id,
        matched_count=run.matched_count,
        changed_count=run.changed_count,
        unchanged_count=run.unchanged_count,
        manual_skip_count=run.manual_skip_count,
        conflict_skip_count=run.conflict_skip_count,
    )


def simulate_rules(session: Session, workspace_id: int, context: RuleContext) -> RuleSimulation:
    """Evaluate every workspace rule and established fallback without changing state."""
    with session.no_autoflush:
        compiled = load_compiled_rule_set(session, workspace_id)
        matches = tuple(
            WorkspaceRuleMatch(rule, result)
            for rule in compiled.rules
            if (result := evaluate_condition(rule.condition, context)).matched
        )
        if matches:
            winner = matches[0]
            rule = winner.rule
            decision = CategorizationDecision(
                normalized_merchant=rule.normalized_merchant
                or merchant_display_fallback(context.description),
                category_id=rule.category_id,
                is_subscription=rule.is_subscription,
                source=CategorizationSource.WORKSPACE_RULE,
                tag_ids=rule.tag_ids,
                billing_period_months=rule.billing_period_months,
                merchant_rule_id=rule.id,
                explanation=winner.explanation,
            )
        else:
            candidate = NormalizedTransaction(
                row_number=0,
                transaction_date=context.transaction_date,
                description=context.description,
                normalized_merchant=merchant_display_fallback(context.description),
                amount_cents=context.amount_cents,
            )
            decision = categorize_candidate(
                session,
                workspace_id,
                candidate,
                provider_key=context.provider_key,
                account_id=context.account_id,
                workspace_rules=CompiledWorkspaceRuleSet(workspace_id, ()),
            )
    return RuleSimulation(matches, decision)


def preview_rule_deletion(session: Session, workspace_id: int, rule_id: int) -> RuleDeletionPreview:
    """Count definite post-delete next-rule and fallback outcomes without changing state."""
    get_rule(session, workspace_id, rule_id)
    compiled = load_compiled_rule_set(session, workspace_id)
    target_index = next(
        (index for index, rule in enumerate(compiled.rules) if rule.id == rule_id),
        None,
    )
    if target_index is None:
        return RuleDeletionPreview(0, 0, 0, 0)

    target_winner_count = 0
    next_rule_match_count = 0
    fallback_match_count = 0
    unavailable_count = 0
    for transaction in _preview_transactions(session, workspace_id):
        context = _rule_context(transaction)
        winner_index, ambiguous = _historical_winner_index(compiled.rules, context)
        if ambiguous:
            unavailable_count += 1
            continue
        if winner_index != target_index:
            continue
        target_winner_count += 1
        lower_index, lower_ambiguous = _historical_winner_index(
            compiled.rules[target_index + 1 :], context
        )
        if lower_ambiguous:
            unavailable_count += 1
        elif lower_index is None:
            fallback_match_count += 1
        else:
            next_rule_match_count += 1
    return RuleDeletionPreview(
        target_winner_count,
        next_rule_match_count,
        fallback_match_count,
        unavailable_count,
    )


def _historical_winner_index(
    rules: tuple[CompiledWorkspaceRule, ...], context: RuleContext
) -> tuple[int | None, bool]:
    unknown_before_winner = False
    for index, rule in enumerate(rules):
        outcome = _evaluate_historical_condition(rule.condition, context)
        if outcome is None:
            unknown_before_winner = True
        elif outcome:
            return (None, True) if unknown_before_winner else (index, False)
    return (None, unknown_before_winner)


def preview_rule_impact(
    session: Session,
    workspace_id: int,
    draft: RuleDraft,
    *,
    exclude_rule_id: int | None,
) -> RuleImpactPreview:
    """Analyze a draft against authorized history with no writes or raw examples."""
    with session.no_autoflush:
        values = _validated_draft(session, workspace_id, draft)
        existing = load_compiled_rule_set(session, workspace_id)
        active_rules = tuple(rule for rule in existing.rules if rule.id != exclude_rule_id)
        higher_rule_ids = _higher_priority_rule_ids(
            session,
            workspace_id,
            active_rules,
            exclude_rule_id=exclude_rule_id,
        )
        draft_rule = CompiledWorkspaceRule(
            id=0,
            name=values.name,
            normalized_merchant=values.normalized_merchant,
            category_id=values.category_id,
            is_subscription=values.is_subscription,
            billing_period_months=values.billing_period_months,
            tag_ids=tuple(tag.id for tag in values.tags),
            condition=values.condition,
        )

        matched_count = 0
        would_change_count = 0
        unchanged_count = 0
        manual_skip_count = 0
        conflict_skip_count = 0
        not_matched_count = 0
        unavailable_count = 0
        limitation_codes: set[str] = set()
        category_counter: Counter[tuple[int | None, str]] = Counter()
        account_counter: Counter[tuple[int | None, str]] = Counter()
        conflict_counter: Counter[tuple[int, str]] = Counter()
        examples: list[PreviewExample] = []
        higher_rules = tuple(rule for rule in active_rules if rule.id in higher_rule_ids)

        for transaction in _preview_transactions(session, workspace_id):
            context = _rule_context(transaction)
            draft_outcome = _evaluate_historical_condition(draft_rule.condition, context)
            if draft_outcome is None:
                unavailable_count += 1
                limitation_codes.add(PREVIEW_LIMITATION_PROVIDER_UNAVAILABLE)
                continue
            if not draft_outcome:
                not_matched_count += 1
                continue

            matched_count += 1
            category_counter[(transaction.category_id, transaction.category_label)] += 1
            account_counter[(transaction.account_id, transaction.account_label)] += 1

            outcome: str
            winning_rule_id: int | None = None
            if transaction.categorization_source == CategorizationSource.MANUAL.value:
                manual_skip_count += 1
                outcome = "manual_protected"
            else:
                winning_conflict, conflict_unknown = _historical_conflict_winner(
                    higher_rules, context
                )
                if conflict_unknown:
                    unavailable_count += 1
                    limitation_codes.add(PREVIEW_LIMITATION_PROVIDER_UNAVAILABLE)
                    continue
                if winning_conflict is not None:
                    conflict_skip_count += 1
                    winning_rule_id = winning_conflict.id
                    conflict_counter[(winning_conflict.id, winning_conflict.name)] += 1
                    outcome = "shadowed"
                elif _actions_are_identical(transaction, draft_rule):
                    unchanged_count += 1
                    outcome = "unchanged"
                else:
                    would_change_count += 1
                    outcome = "would_change"

            if len(examples) < MAX_PREVIEW_EXAMPLES:
                examples.append(
                    PreviewExample(
                        transaction_id=transaction.id,
                        description=sanitize_transaction_description(transaction.description),
                        outcome=outcome,
                        winning_rule_id=winning_rule_id,
                    )
                )

        conflicts = tuple(
            PreviewConflict(rule_id, name, count)
            for (rule_id, name), count in sorted(
                conflict_counter.items(),
                key=lambda item: _conflict_sort_key(item, active_rules),
            )
        )
        return RuleImpactPreview(
            matched_count=matched_count,
            would_change_count=would_change_count,
            unchanged_count=unchanged_count,
            manual_skip_count=manual_skip_count,
            conflict_skip_count=conflict_skip_count,
            not_matched_count=not_matched_count,
            unavailable_count=unavailable_count,
            limitation_codes=tuple(sorted(limitation_codes)),
            category_counts=_group_counts(category_counter),
            account_counts=_group_counts(account_counter),
            conflicts=conflicts,
            examples=tuple(examples),
        )


def _evaluate_historical_condition(node: ConditionNode, context: RuleContext) -> bool | None:
    """Evaluate with unknown provider provenance preserved through boolean operators."""
    if isinstance(node, PredicateCondition):
        if node.field == "provider_key":
            return None
        return evaluate_condition(node, context).matched
    if isinstance(node, AllCondition):
        unknown = False
        for child in node.children:
            outcome = _evaluate_historical_condition(child, context)
            if outcome is False:
                return False
            unknown = unknown or outcome is None
        return None if unknown else True
    if isinstance(node, AnyCondition):
        unknown = False
        for child in node.children:
            outcome = _evaluate_historical_condition(child, context)
            if outcome is True:
                return True
            unknown = unknown or outcome is None
        return None if unknown else False
    if isinstance(node, NotCondition):
        outcome = _evaluate_historical_condition(node.child, context)
        return None if outcome is None else not outcome
    return False


def _historical_conflict_winner(
    higher_rules: tuple[CompiledWorkspaceRule, ...],
    context: RuleContext,
) -> tuple[CompiledWorkspaceRule | None, bool]:
    unknown_before_winner = False
    for rule in higher_rules:
        outcome = _evaluate_historical_condition(rule.condition, context)
        if outcome is None:
            unknown_before_winner = True
        elif outcome:
            if unknown_before_winner:
                return None, True
            return rule, False
    return None, unknown_before_winner


def _higher_priority_rule_ids(
    session: Session,
    workspace_id: int,
    active_rules: tuple[CompiledWorkspaceRule, ...],
    *,
    exclude_rule_id: int | None,
) -> frozenset[int]:
    if exclude_rule_id is None:
        return frozenset(rule.id for rule in active_rules)
    target = get_rule(session, workspace_id, exclude_rule_id)
    target_order = (target.priority, target.id)
    return frozenset(
        rule.id
        for rule in list_rules(session, workspace_id)
        if rule.enabled and rule.id != target.id and (rule.priority, rule.id) < target_order
    )


def _preview_transactions(session: Session, workspace_id: int) -> Iterator[_PreviewTransaction]:
    statement = (
        select(
            Transaction.id.label("transaction_id"),
            Transaction.date.label("transaction_date"),
            Transaction.description,
            Transaction.normalized_merchant,
            Transaction.amount_cents,
            Transaction.category_id.label("stored_category_id"),
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            Transaction.categorization_source,
            Transaction.is_subscription,
            Transaction.billing_period_months,
            Transaction.import_job_id.label("stored_import_job_id"),
            ImportJob.id.label("accessible_import_job_id"),
            ImportJob.account_id.label("stored_account_id"),
            Account.id.label("account_id"),
            Account.name.label("account_name"),
            transaction_tags.c.tag_id,
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
        .outerjoin(
            Category,
            and_(
                Category.id == Transaction.category_id,
                or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
            ),
        )
        .outerjoin(transaction_tags, transaction_tags.c.transaction_id == Transaction.id)
        .where(Transaction.workspace_id == workspace_id)
        .order_by(Transaction.id, transaction_tags.c.tag_id)
        .execution_options(yield_per=500)
    )
    current: dict[str, object] | None = None
    tag_ids: list[int] = []
    for row in session.execute(statement).mappings():
        transaction_id = int(row["transaction_id"])
        if current is not None and transaction_id != current["transaction_id"]:
            yield _projected_transaction(current, tag_ids)
            current = None
            tag_ids = []
        if current is None:
            current = dict(row)
        if row["tag_id"] is not None:
            tag_ids.append(int(row["tag_id"]))
    if current is not None:
        yield _projected_transaction(current, tag_ids)


def _projected_transaction(values: dict[str, object], tag_ids: list[int]) -> _PreviewTransaction:
    transaction_date = values["transaction_date"]
    assert isinstance(transaction_date, datetime)
    category_unavailable = (
        values["stored_category_id"] is not None and values["category_id"] is None
    )
    account_unavailable = (
        values["stored_import_job_id"] is not None and values["accessible_import_job_id"] is None
    ) or (values["stored_account_id"] is not None and values["account_id"] is None)
    return _PreviewTransaction(
        id=int(values["transaction_id"]),
        transaction_date=transaction_date,
        description=str(values["description"]),
        normalized_merchant=(
            str(values["normalized_merchant"])
            if values["normalized_merchant"] is not None
            else None
        ),
        amount_cents=int(values["amount_cents"]),
        category_id=(int(values["category_id"]) if values["category_id"] is not None else None),
        category_label=(
            "Unavailable category"
            if category_unavailable
            else str(values["category_name"])
            if values["category_name"] is not None
            else "Uncategorized"
        ),
        categorization_source=str(values["categorization_source"]),
        is_subscription=bool(values["is_subscription"]),
        billing_period_months=(
            int(values["billing_period_months"])
            if values["billing_period_months"] is not None
            else None
        ),
        account_id=(int(values["account_id"]) if values["account_id"] is not None else None),
        account_label=(
            "Unavailable account"
            if account_unavailable
            else str(values["account_name"])
            if values["account_name"] is not None
            else "No account"
        ),
        tag_ids=tuple(tag_ids),
    )


def _rule_context(transaction: _PreviewTransaction) -> RuleContext:
    return RuleContext(
        description=transaction.description,
        merchant_key=merchant_key(transaction.description),
        amount_cents=transaction.amount_cents,
        transaction_date=transaction.transaction_date.date(),
        direction=(
            "income"
            if transaction.amount_cents > 0
            else "expense"
            if transaction.amount_cents < 0
            else "zero"
        ),
        account_id=transaction.account_id,
        provider_key=None,
    )


def _actions_are_identical(
    transaction: _PreviewTransaction, draft_rule: CompiledWorkspaceRule
) -> bool:
    expected_merchant = draft_rule.normalized_merchant or merchant_display_fallback(
        transaction.description
    )
    return (
        transaction.normalized_merchant == expected_merchant
        and transaction.category_id == draft_rule.category_id
        and transaction.is_subscription == draft_rule.is_subscription
        and transaction.billing_period_months == draft_rule.billing_period_months
        and tuple(sorted(transaction.tag_ids)) == tuple(sorted(draft_rule.tag_ids))
    )


def _group_counts(
    counter: Counter[tuple[int | None, str]],
) -> tuple[PreviewGroupCount, ...]:
    return tuple(
        PreviewGroupCount(group_id, label, count)
        for (group_id, label), count in sorted(
            counter.items(),
            key=lambda item: (
                -item[1],
                item[0][1].casefold(),
                -1 if item[0][0] is None else item[0][0],
            ),
        )
    )


def _conflict_sort_key(
    item: tuple[tuple[int, str], int],
    active_rules: tuple[CompiledWorkspaceRule, ...],
) -> tuple[int, int]:
    order = {rule.id: index for index, rule in enumerate(active_rules)}
    (rule_id, _name), count = item
    return (order.get(rule_id, len(active_rules)), -count)


def list_rules(session: Session, workspace_id: int) -> tuple[MerchantRule, ...]:
    """Return one workspace's rules in deterministic execution order."""
    return tuple(
        session.scalars(
            select(MerchantRule)
            .where(MerchantRule.workspace_id == workspace_id)
            .order_by(MerchantRule.priority, MerchantRule.id)
        )
    )


def get_rule(session: Session, workspace_id: int, rule_id: int) -> MerchantRule:
    """Load a rule only through its active workspace boundary."""
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.id == rule_id,
            MerchantRule.workspace_id == workspace_id,
        )
    )
    if rule is None:
        raise RuleNotFoundError("Rule not found.")
    return rule


def create_rule(session: Session, workspace_id: int, draft: RuleDraft) -> MerchantRule:
    """Validate, append, flush, and return a new enabled workspace rule."""
    _serialize_workspace_ordering(session, workspace_id)
    values = _validated_draft(session, workspace_id, draft)
    existing = list(list_rules(session, workspace_id))
    _assign_compact_priorities(existing)
    rule = MerchantRule(
        workspace_id=workspace_id,
        merchant_pattern=None,
        name=values.name,
        enabled=True,
        priority=len(existing),
        condition_version=1,
        condition_json=values.condition_json,
        lock_version=1,
        normalized_merchant=values.normalized_merchant,
        category_id=values.category_id,
        is_subscription=values.is_subscription,
        billing_period_months=values.billing_period_months,
        tags=list(values.tags),
    )
    session.add(rule)
    session.flush()
    return rule


def normalize_rule_draft(session: Session, workspace_id: int, draft: RuleDraft) -> RuleDraft:
    """Return the exact validated values lifecycle writes persist for an untrusted draft."""
    values = _validated_draft(session, workspace_id, draft)
    return RuleDraft(
        name=values.name,
        condition=values.condition,
        normalized_merchant=values.normalized_merchant,
        category_id=values.category_id,
        tag_ids=tuple(tag.id for tag in values.tags),
        is_subscription=values.is_subscription,
        billing_period_months=values.billing_period_months,
    )


def update_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    draft: RuleDraft,
    *,
    expected_lock_version: int,
) -> MerchantRule:
    """Atomically replace mutable rule values when the submitted version is current."""
    _serialize_workspace_ordering(session, workspace_id)
    rule = get_rule(session, workspace_id, rule_id)
    _validate_lock_version(expected_lock_version)
    values = _validated_draft(session, workspace_id, draft)
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == rule.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == expected_lock_version,
        )
        .values(
            name=values.name,
            condition_version=1,
            condition_json=values.condition_json,
            lock_version=MerchantRule.lock_version + 1,
            normalized_merchant=values.normalized_merchant,
            category_id=values.category_id,
            is_subscription=values.is_subscription,
            billing_period_months=values.billing_period_months,
        )
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being edited.")
    rule.tags = list(values.tags)
    session.flush()
    return rule


def set_rule_enabled(
    session: Session,
    workspace_id: int,
    rule_id: int,
    enabled: bool,
    *,
    expected_lock_version: int,
) -> MerchantRule:
    """Enable or disable one rule under an optimistic-lock check."""
    _serialize_workspace_ordering(session, workspace_id)
    rule = get_rule(session, workspace_id, rule_id)
    _validate_lock_version(expected_lock_version)
    if type(enabled) is not bool:
        raise RuleValidationError({"enabled": "Enabled must be a boolean."})
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == rule.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == expected_lock_version,
        )
        .values(enabled=enabled, lock_version=MerchantRule.lock_version + 1)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being edited.")
    session.flush()
    return rule


def move_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    *,
    new_index: int,
    expected_lock_version: int | None = None,
) -> MerchantRule:
    """Move a rule and compact all priorities inside the caller's transaction."""
    _serialize_workspace_ordering(session, workspace_id)
    moved = get_rule(session, workspace_id, rule_id)
    ordered = list(list_rules(session, workspace_id))
    if type(new_index) is not int or not 0 <= new_index < len(ordered):
        raise RuleValidationError({"new_index": "Choose a valid rule position."})
    if expected_lock_version is not None:
        _validate_lock_version(expected_lock_version)
    current_version = moved.lock_version if expected_lock_version is None else expected_lock_version
    result = session.execute(
        update(MerchantRule)
        .where(
            MerchantRule.id == moved.id,
            MerchantRule.workspace_id == workspace_id,
            MerchantRule.lock_version == current_version,
        )
        .values(lock_version=MerchantRule.lock_version + 1)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount != 1:
        raise RuleConflictError("The rule changed while it was being reordered.")
    ordered.remove(moved)
    ordered.insert(new_index, moved)
    _assign_compact_priorities(ordered)
    session.flush()
    return moved


def duplicate_rule(session: Session, workspace_id: int, rule_id: int) -> MerchantRule:
    """Copy a scoped rule and insert the copy immediately after its source."""
    _serialize_workspace_ordering(session, workspace_id)
    source = get_rule(session, workspace_id, rule_id)
    try:
        source_condition = parse_condition(_persisted_condition_payload(source))
    except RuleConditionValidationError:
        raise RuleValidationError({"condition": "Choose a valid rule condition."}) from None
    values = _validated_draft(
        session,
        workspace_id,
        RuleDraft(
            name=_copy_name(source.name),
            condition=source_condition,
            normalized_merchant=source.normalized_merchant,
            category_id=source.category_id,
            tag_ids=tuple(tag.id for tag in source.tags),
            is_subscription=source.is_subscription,
            billing_period_months=source.billing_period_months,
        ),
    )
    ordered = list(list_rules(session, workspace_id))
    source_index = ordered.index(source)
    duplicate = MerchantRule(
        workspace_id=workspace_id,
        merchant_pattern=None,
        name=values.name,
        enabled=source.enabled,
        priority=source_index + 1,
        condition_version=1,
        condition_json=copy.deepcopy(values.condition_json),
        lock_version=1,
        normalized_merchant=values.normalized_merchant,
        category_id=values.category_id,
        is_subscription=values.is_subscription,
        billing_period_months=values.billing_period_months,
        tags=list(values.tags),
    )
    session.add(duplicate)
    session.flush()
    ordered.insert(source_index + 1, duplicate)
    _assign_compact_priorities(ordered)
    session.flush()
    return duplicate


def delete_rule(
    session: Session,
    workspace_id: int,
    rule_id: int,
    *,
    expected_lock_version: int | None = None,
) -> None:
    """Delete a scoped rule and compact remaining priorities without committing."""
    _serialize_workspace_ordering(session, workspace_id)
    rule = get_rule(session, workspace_id, rule_id)
    if expected_lock_version is not None:
        _validate_lock_version(expected_lock_version)
        result = session.execute(
            update(MerchantRule)
            .where(
                MerchantRule.id == rule.id,
                MerchantRule.workspace_id == workspace_id,
                MerchantRule.lock_version == expected_lock_version,
            )
            .values(lock_version=MerchantRule.lock_version + 1)
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            raise RuleConflictError("The rule changed while it was being deleted.")
    session.delete(rule)
    session.flush()
    _assign_compact_priorities(list(list_rules(session, workspace_id)))
    session.flush()


def _validated_draft(session: Session, workspace_id: int, draft: RuleDraft) -> _ValidatedDraft:
    field_errors: dict[str, str] = {}
    name = _normalized_text(draft.name) if isinstance(draft.name, str) else ""
    if not name:
        field_errors["name"] = "Rule name is required."
    elif len(name) > MAX_RULE_NAME_LENGTH:
        field_errors["name"] = "Rule name must be 120 characters or fewer."

    normalized_merchant: str | None
    if draft.normalized_merchant is None:
        normalized_merchant = None
    elif not isinstance(draft.normalized_merchant, str):
        normalized_merchant = None
        field_errors["normalized_merchant"] = "Merchant name must be text."
    else:
        normalized_merchant = _normalized_text(draft.normalized_merchant) or None
        if normalized_merchant is not None and len(normalized_merchant) > MAX_MERCHANT_NAME_LENGTH:
            field_errors["normalized_merchant"] = "Merchant name must be 255 characters or fewer."

    if type(draft.category_id) is not int or draft.category_id <= 0:
        field_errors["category_id"] = "Choose a valid category."
    if type(draft.is_subscription) is not bool:
        field_errors["is_subscription"] = "Subscription must be a boolean."
    cadence = draft.billing_period_months
    if cadence is not None and (type(cadence) is not int or cadence < 1 or cadence > 120):
        field_errors["billing_period_months"] = "Choose a billing cadence between 1 and 120 months."
    if not isinstance(draft.tag_ids, tuple) or any(
        type(tag_id) is not int or tag_id <= 0 for tag_id in draft.tag_ids
    ):
        field_errors["tag_ids"] = "Choose valid tags."

    condition: ConditionNode | None = None
    condition_json: dict[str, object] | None = None
    try:
        condition_json = json.loads(condition_to_json(draft.condition))
        condition = parse_condition(condition_json)
    except (RuleConditionValidationError, TypeError):
        field_errors["condition"] = "Choose a valid rule condition."

    if field_errors:
        raise RuleValidationError(field_errors)
    assert condition is not None
    assert condition_json is not None

    category = session.scalar(
        select(Category).where(
            Category.id == draft.category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )
    if category is None:
        raise RuleResourceNotFoundError("Rule resource not found.")

    unique_tag_ids = tuple(dict.fromkeys(draft.tag_ids))
    effective_tag_ids = tag_ids_with_subscription(
        session,
        unique_tag_ids,
        draft.is_subscription,
    )
    tags = _accessible_tags(session, workspace_id, effective_tag_ids)
    _validate_condition_accounts(session, workspace_id, condition)

    return _ValidatedDraft(
        name=name,
        condition=condition,
        condition_json=condition_json,
        normalized_merchant=normalized_merchant,
        category_id=category.id,
        tags=tags,
        is_subscription=draft.is_subscription,
        billing_period_months=cadence,
    )


def _accessible_tags(
    session: Session, workspace_id: int, tag_ids: tuple[int, ...]
) -> tuple[Tag, ...]:
    if not tag_ids:
        return ()
    unique_ids = tuple(dict.fromkeys(tag_ids))
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
        raise RuleResourceNotFoundError("Rule resource not found.")
    return tags


def _validate_condition_accounts(
    session: Session, workspace_id: int, condition: ConditionNode
) -> None:
    account_ids = set(_condition_account_ids(condition))
    if not account_ids:
        return
    accessible_ids = set(
        session.scalars(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.workspace_id == workspace_id,
            )
        )
    )
    if accessible_ids != account_ids:
        raise RuleResourceNotFoundError("Rule resource not found.")


def _condition_account_ids(condition: ConditionNode) -> tuple[int, ...]:
    if isinstance(condition, PredicateCondition):
        if condition.field == "account_id" and type(condition.value) is int:
            return (condition.value,)
        return ()
    if isinstance(condition, (AllCondition, AnyCondition)):
        return tuple(
            account_id
            for child in condition.children
            for account_id in _condition_account_ids(child)
        )
    if isinstance(condition, NotCondition):
        return _condition_account_ids(condition.child)
    return ()


def _assign_compact_priorities(rules: list[MerchantRule]) -> None:
    for priority, rule in enumerate(rules):
        rule.priority = priority


def _serialize_workspace_ordering(session: Session, workspace_id: int) -> None:
    """Lock the workspace before reading or rewriting its ordered rule collection."""
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = session.execute(
            update(Workspace)
            .where(Workspace.id == workspace_id)
            .values(name=Workspace.name)
            .execution_options(synchronize_session=False)
        )
        found = result.rowcount == 1
    else:
        found = (
            session.scalar(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
            )
            is not None
        )
    if not found:
        raise RuleNotFoundError("Rule not found.")


def _validate_lock_version(version: int) -> None:
    if type(version) is not int or version <= 0:
        raise RuleConflictError("The rule changed while it was being edited.")


def _copy_name(name: str) -> str:
    base = name[: MAX_RULE_NAME_LENGTH - len(" copy")].rstrip()
    return f"{base} copy"


def _persisted_condition_payload(rule: MerchantRule) -> object:
    if rule.condition_json == {} and rule.merchant_pattern:
        return {
            "version": 1,
            "type": "predicate",
            "field": "merchant_key",
            "operator": "exact",
            "value": rule.merchant_pattern,
        }
    return rule.condition_json


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())
