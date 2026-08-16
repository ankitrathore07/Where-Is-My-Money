from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.categorization.types import CategorizationSource
from app.db.models import (
    Account,
    Base,
    Category,
    ImportJob,
    MerchantRule,
    RuleApplicationRun,
    Tag,
    Transaction,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.rules.application_tokens import (
    ApplicationTokenPayload,
    canonical_application_selection,
    load_application_token,
)
from app.rules.service import (
    HistoryFilters,
    RuleDraft,
    RuleNotFoundError,
    RuleResourceNotFoundError,
    RuleValidationError,
    StaleRuleApplicationError,
    confirm_historical_application,
    preview_historical_application,
    set_rule_enabled,
    update_rule,
)
from app.rules.types import PredicateCondition
from app.rules.validation import condition_to_json
from app.transactions.service import upsert_workspace_rule

SECRET = "history-application-secret"


@pytest.fixture(autouse=True)
def _authorize_workspace_owner(session: Session, workspace: Workspace) -> None:
    session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=workspace.owner_id,
            role="member",
        )
    )
    session.flush()


def _category(session: Session, workspace_id: int | None, name: str) -> Category:
    category = Category(
        workspace_id=workspace_id,
        name=name,
        name_key=name.casefold(),
        kind="expense",
    )
    session.add(category)
    session.flush()
    return category


def _tag(session: Session, workspace_id: int | None, name: str) -> Tag:
    tag = Tag(workspace_id=workspace_id, name=name, name_key=name.casefold())
    session.add(tag)
    session.flush()
    return tag


def _account(session: Session, workspace_id: int, name: str) -> tuple[Account, ImportJob]:
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type="checking",
        institution_key="other",
        institution="Local",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    job = ImportJob(workspace_id=workspace_id, account_id=account.id, status="committed")
    session.add(job)
    session.flush()
    return account, job


def _rule(
    session: Session,
    workspace_id: int,
    category_id: int,
    *,
    name: str = "Coffee history",
    priority: int = 1,
    field: str = "description",
    operator: str = "contains",
    value: object = "COFFEE",
    normalized_merchant: str | None = "Coffee Club",
    tags: tuple[Tag, ...] = (),
    is_subscription: bool = False,
    cadence: int | None = None,
) -> MerchantRule:
    rule = MerchantRule(
        workspace_id=workspace_id,
        merchant_pattern=None,
        name=name,
        enabled=True,
        priority=priority,
        condition_version=1,
        condition_json=json.loads(condition_to_json(PredicateCondition(field, operator, value))),
        lock_version=1,
        normalized_merchant=normalized_merchant,
        category_id=category_id,
        is_subscription=is_subscription,
        billing_period_months=cadence,
        tags=list(tags),
    )
    session.add(rule)
    session.flush()
    return rule


def _transaction(
    session: Session,
    workspace_id: int,
    category_id: int | None,
    *,
    description: str,
    day: int = 15,
    amount_cents: int = -1_250,
    merchant: str | None = "Old merchant",
    source: str = CategorizationSource.UNCATEGORIZED.value,
    merchant_rule_id: int | None = None,
    tags: tuple[Tag, ...] = (),
    is_subscription: bool = False,
    cadence: int | None = None,
    import_job_id: int | None = None,
    fingerprint: str | None = None,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, day, 12, 30, tzinfo=UTC),
        description=description,
        normalized_merchant=merchant,
        amount_cents=amount_cents,
        category_id=category_id,
        categorization_source=source,
        merchant_rule_id=merchant_rule_id,
        tags=list(tags),
        is_subscription=is_subscription,
        billing_period_months=cadence,
        import_job_id=import_job_id,
        duplicate_fingerprint=fingerprint,
    )
    session.add(transaction)
    session.flush()
    return transaction


def test_preview_normalizes_authorized_filters_and_classifies_full_rule_order(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if filters escape scope or manual/shadow/identical rows become selectable."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Coffee")
    higher_target = _category(session, workspace.id, "VIP")
    target_tag = _tag(session, workspace.id, "Morning")
    account, job = _account(session, workspace.id, "Household checking")
    _foreign_account, foreign_job = _account(session, other_workspace.id, "Private account")
    higher = _rule(
        session,
        workspace.id,
        higher_target.id,
        name="VIP winner",
        priority=0,
        operator="exact",
        value="COFFEE VIP",
    )
    rule = _rule(
        session,
        workspace.id,
        target.id,
        tags=(target_tag,),
        is_subscription=True,
        cadence=1,
    )
    eligible = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE SHOP",
        import_job_id=job.id,
    )
    _transaction(
        session,
        workspace.id,
        target.id,
        description="COFFEE SAME",
        merchant="Coffee Club",
        source=CategorizationSource.WORKSPACE_RULE.value,
        merchant_rule_id=rule.id,
        tags=(target_tag,),
        is_subscription=True,
        cadence=1,
        import_job_id=job.id,
    )
    _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE MANUAL",
        source=CategorizationSource.MANUAL.value,
        import_job_id=job.id,
    )
    _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE VIP",
        import_job_id=job.id,
    )
    _transaction(
        session,
        workspace.id,
        current.id,
        description="TEA SHOP",
        import_job_id=job.id,
    )
    _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE OUTSIDE DATE",
        day=1,
        import_job_id=job.id,
    )
    _transaction(
        session,
        other_workspace.id,
        current.id,
        description="COFFEE PRIVATE",
        import_job_id=foreign_job.id,
    )
    session.commit()

    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(
            date_from="2026-08-10",
            date_to=date(2026, 8, 31),
            account_id=account.id,
            direction="expense",
        ),
        selected_transaction_ids=(eligible.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )

    assert preview.normalized_filters == {
        "account_id": account.id,
        "category_id": None,
        "date_from": "2026-08-10",
        "date_to": "2026-08-31",
        "direction": "expense",
    }
    assert preview.matched_count == 4
    assert preview.would_change_count == 1
    assert preview.unchanged_count == 1
    assert preview.manual_skip_count == 1
    assert preview.conflict_skip_count == 1
    assert preview.not_matched_count == 1
    assert preview.unavailable_count == 0
    assert preview.eligible_transaction_ids == (eligible.id,)
    assert preview.selected_transaction_ids == (eligible.id,)
    payload = load_application_token(SECRET, preview.token)
    assert payload.selected_transaction_ids == (eligible.id,)
    assert payload.normalized_filters == preview.normalized_filters

    run = session.get(RuleApplicationRun, preview.run_id)
    assert run is not None
    assert run.status == "previewed"
    assert run.rule_name_snapshot == rule.name
    assert run.changed_count == 1
    assert run.selection_json == {
        "normalized_filters": preview.normalized_filters,
        "selected_transaction_ids": (eligible.id,),
    }
    audit_text = repr(run.selection_json)
    assert "COFFEE" not in audit_text
    assert "1250" not in audit_text
    assert higher.id != rule.id


def test_confirm_applies_exact_actions_preserves_source_data_and_is_idempotent(
    session: Session, workspace: Workspace
) -> None:
    """Break if confirmation changes immutable inputs or a retry reports a new outcome."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    desired_tag = _tag(session, workspace.id, "Desired")
    old_tag = _tag(session, workspace.id, "Old")
    _account_row, job = _account(session, workspace.id, "Checking")
    rule = _rule(
        session,
        workspace.id,
        target.id,
        normalized_merchant=None,
        tags=(desired_tag,),
        is_subscription=True,
        cadence=3,
    )
    transaction = _transaction(
        session,
        workspace.id,
        current.id,
        description="  Coffee   Kiosk  ",
        merchant="Original merchant",
        tags=(old_tag,),
        import_job_id=job.id,
        fingerprint="f" * 64,
    )
    session.commit()
    session.refresh(transaction)
    immutable = (
        transaction.date,
        transaction.description,
        transaction.amount_cents,
        transaction.import_job_id,
        transaction.duplicate_fingerprint,
    )
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )

    first = confirm_historical_application(
        session,
        workspace.id,
        preview.token,
        workspace.owner_id,
        secret_key=SECRET,
    )
    second = confirm_historical_application(
        session,
        workspace.id,
        preview.token,
        workspace.owner_id,
        secret_key=SECRET,
    )

    session.refresh(transaction)
    assert second == first
    assert first.run_id == preview.run_id
    assert first.changed_count == 1
    assert transaction.normalized_merchant == "Coffee Kiosk"
    assert transaction.category_id == target.id
    assert transaction.is_subscription is True
    assert transaction.billing_period_months == 3
    assert transaction.categorization_source == CategorizationSource.WORKSPACE_RULE.value
    assert transaction.merchant_rule_id == rule.id
    assert [tag.id for tag in transaction.tags] == [desired_tag.id]
    assert (
        transaction.date,
        transaction.description,
        transaction.amount_cents,
        transaction.import_job_id,
        transaction.duplicate_fingerprint,
    ) == immutable
    run = session.get(RuleApplicationRun, preview.run_id)
    assert run is not None
    assert run.status == "confirmed"
    assert run.changed_count == 1
    assert run.confirmed_at is not None
    assert len(session.scalars(select(RuleApplicationRun)).all()) == 1


def test_confirm_rejects_changed_state_before_mutating_any_selected_row(
    session: Session, workspace: Workspace
) -> None:
    """Break if digest validation happens after an earlier selected row is mutated."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    unexpected = _category(session, workspace.id, "Unexpected")
    rule = _rule(session, workspace.id, target.id)
    first = _transaction(
        session, workspace.id, current.id, description="COFFEE FIRST", merchant="First old"
    )
    stale = _transaction(
        session, workspace.id, current.id, description="COFFEE SECOND", merchant="Second old"
    )
    manual = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE MANUAL",
        source=CategorizationSource.MANUAL.value,
    )
    session.commit()
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(first.id, stale.id),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    stale.category_id = unexpected.id
    session.flush()

    with pytest.raises(StaleRuleApplicationError):
        confirm_historical_application(
            session,
            workspace.id,
            preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )

    session.refresh(first)
    session.refresh(manual)
    assert first.normalized_merchant == "First old"
    assert first.category_id == current.id
    assert first.categorization_source == CategorizationSource.UNCATEGORIZED.value
    assert first.merchant_rule_id is None
    assert manual.categorization_source == CategorizationSource.MANUAL.value
    run = session.get(RuleApplicationRun, preview.run_id)
    assert run is not None
    assert run.status == "stale"
    assert run.confirmed_at is None


def test_stale_audit_exposes_run_metadata_and_obeys_caller_commit_or_rollback(
    session: Session, workspace: Workspace
) -> None:
    """Break if a 409 mapper cannot durably commit stale status without a service commit."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    committed_transaction = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE COMMIT STALE",
    )
    rolled_back_transaction = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE ROLLBACK STALE",
    )
    session.commit()

    committed_preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(committed_transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    session.commit()
    committed_transaction.normalized_merchant = "Changed after preview"
    session.commit()

    with pytest.raises(StaleRuleApplicationError) as committed_error:
        confirm_historical_application(
            session,
            workspace.id,
            committed_preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )
    assert committed_error.value.run_id == committed_preview.run_id
    assert committed_error.value.status == "stale"
    session.commit()

    with Session(bind=session.get_bind()) as fresh:
        committed_run = fresh.get(RuleApplicationRun, committed_preview.run_id)
        assert committed_run is not None
        assert committed_run.status == "stale"

    rolled_back_preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(rolled_back_transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    session.commit()
    rolled_back_transaction.normalized_merchant = "Changed then rolled back"
    session.commit()

    with pytest.raises(StaleRuleApplicationError) as rolled_back_error:
        confirm_historical_application(
            session,
            workspace.id,
            rolled_back_preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )
    assert rolled_back_error.value.run_id == rolled_back_preview.run_id
    assert rolled_back_error.value.status == "stale"
    session.rollback()

    with Session(bind=session.get_bind()) as fresh:
        rolled_back_run = fresh.get(RuleApplicationRun, rolled_back_preview.run_id)
        assert rolled_back_run is not None
        assert rolled_back_run.status == "previewed"


def test_confirmation_recomputes_full_order_and_detects_new_shadowing_rule(
    session: Session, workspace: Workspace
) -> None:
    """Break if confirmation checks only the selected rule and ignores precedence drift."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    higher_target = _category(session, workspace.id, "Higher")
    rule = _rule(session, workspace.id, target.id, priority=1)
    transaction = _transaction(session, workspace.id, current.id, description="COFFEE PRIORITY")
    session.commit()
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    _rule(
        session,
        workspace.id,
        higher_target.id,
        name="New higher winner",
        priority=0,
    )
    session.flush()

    with pytest.raises(StaleRuleApplicationError):
        confirm_historical_application(
            session,
            workspace.id,
            preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )

    session.refresh(transaction)
    assert transaction.category_id == current.id
    assert transaction.merchant_rule_id is None


def test_preview_rejects_foreign_resources_ineligible_selections_and_over_500(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if untrusted filters or selected IDs cross scope or bypass the hard bound."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    foreign_category = _category(session, other_workspace.id, "Private category")
    foreign_account, _foreign_job = _account(session, other_workspace.id, "Private account")
    rule = _rule(session, workspace.id, target.id)
    manual = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE MANUAL",
        source=CategorizationSource.MANUAL.value,
    )
    foreign = _transaction(
        session, other_workspace.id, foreign_category.id, description="COFFEE PRIVATE"
    )
    session.commit()

    for selected_id in (manual.id, foreign.id):
        with pytest.raises(RuleNotFoundError):
            preview_historical_application(
                session,
                workspace.id,
                rule.id,
                HistoryFilters(),
                selected_transaction_ids=(selected_id,),
                user_id=workspace.owner_id,
                secret_key=SECRET,
            )
    with pytest.raises(RuleResourceNotFoundError):
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            HistoryFilters(account_id=foreign_account.id),
            user_id=workspace.owner_id,
            secret_key=SECRET,
        )
    with pytest.raises(RuleResourceNotFoundError):
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            HistoryFilters(category_id=foreign_category.id),
            user_id=workspace.owner_id,
            secret_key=SECRET,
        )
    with pytest.raises(RuleValidationError) as caught:
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            HistoryFilters(),
            selected_transaction_ids=tuple(range(1, 502)),
            user_id=workspace.owner_id,
            secret_key=SECRET,
        )
    assert "selected_transaction_ids" in caught.value.field_errors


def test_preview_requires_initiator_membership_and_writes_no_foreign_audit(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if any existing user can operate on and audit another workspace's history."""
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)

    with pytest.raises(RuleNotFoundError):
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            HistoryFilters(),
            user_id=other_workspace.owner_id,
            secret_key=SECRET,
        )

    assert session.scalar(select(RuleApplicationRun)) is None


def test_preview_requires_explicit_initiator_before_any_workspace_or_selection_lookup(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if missing/foreign identity can use rule, filter, or selection errors as an oracle."""
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    session.flush()

    with pytest.raises(TypeError):
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            HistoryFilters(),
            secret_key=SECRET,
        )

    for malformed_user_id in (None, True, 0, other_workspace.owner_id):
        statements: list[str] = []

        @event.listens_for(session.get_bind(), "before_cursor_execute")
        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _many,
            captured=statements,
        ):
            captured.append(" ".join(statement.casefold().split()))

        try:
            with pytest.raises(RuleNotFoundError, match="Rule not found"):
                preview_historical_application(
                    session,
                    workspace.id,
                    rule.id,
                    HistoryFilters(direction=True),  # type: ignore[arg-type]
                    selected_transaction_ids=(True,),  # type: ignore[arg-type]
                    user_id=malformed_user_id,  # type: ignore[arg-type]
                    secret_key=SECRET,
                )
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

        assert all("merchant_rules" not in statement for statement in statements)
        assert all("categories" not in statement for statement in statements)
        assert all("accounts" not in statement for statement in statements)
        assert all("rule_application_runs" not in statement for statement in statements)
        if type(malformed_user_id) is int and malformed_user_id > 0:
            assert len(statements) == 1
            assert "workspace_memberships" in statements[0]
        else:
            assert statements == []


def test_confirm_checks_membership_before_loading_untrusted_token(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if token validity or workspace state is observable before membership authorization."""
    workspace_id = workspace.id
    foreign_user_id = other_workspace.owner_id
    for malformed_user_id in (None, True, 0, foreign_user_id):
        statements: list[str] = []

        @event.listens_for(session.get_bind(), "before_cursor_execute")
        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _many,
            captured=statements,
        ):
            captured.append(" ".join(statement.casefold().split()))

        try:
            with pytest.raises(RuleNotFoundError, match="Rule not found"):
                confirm_historical_application(
                    session,
                    workspace_id,
                    "invalid-token",
                    malformed_user_id,  # type: ignore[arg-type]
                    secret_key=SECRET,
                )
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

        if type(malformed_user_id) is int and malformed_user_id > 0:
            assert len(statements) == 1
            assert "workspace_memberships" in statements[0]
            assert "rule_application_runs" not in statements[0]
        else:
            assert statements == []


def test_invalid_target_and_matching_invalid_higher_actions_fail_closed(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
) -> None:
    """Break if inaccessible action diagnostics disappear from historical precedence."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    foreign = _category(session, other_workspace.id, "Private")
    transaction = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE INVALID ACTION",
    )
    invalid_higher = _rule(
        session,
        workspace.id,
        foreign.id,
        name="Invalid higher",
        priority=0,
    )
    valid_target = _rule(session, workspace.id, target.id, priority=1)
    session.commit()

    higher_preview = preview_historical_application(
        session,
        workspace.id,
        valid_target.id,
        HistoryFilters(),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    assert higher_preview.matched_count == 1
    assert higher_preview.invalid_count == 1
    assert higher_preview.would_change_count == 0
    assert higher_preview.selected_transaction_ids == ()

    invalid_higher.enabled = False
    valid_target.enabled = False
    invalid_target = _rule(
        session,
        workspace.id,
        foreign.id,
        name="Invalid target",
        priority=2,
    )
    session.flush()
    target_preview = preview_historical_application(
        session,
        workspace.id,
        invalid_target.id,
        HistoryFilters(),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    assert target_preview.matched_count == 1
    assert target_preview.invalid_count == 1
    assert target_preview.would_change_count == 0
    assert transaction.id not in target_preview.selected_transaction_ids


@pytest.mark.parametrize("mutation", ["update", "toggle"])
def test_rule_mutations_share_confirmation_workspace_serialization_key(
    session: Session,
    workspace: Workspace,
    mutation: str,
) -> None:
    """Break if a higher-rule edit can race after confirmation's full-order recomputation."""
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    session.commit()
    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.casefold().split()))

    try:
        if mutation == "update":
            update_rule(
                session,
                workspace.id,
                rule.id,
                RuleDraft(
                    name="Updated",
                    condition=PredicateCondition("description", "contains", "COFFEE"),
                    normalized_merchant="Updated",
                    category_id=target.id,
                ),
                expected_lock_version=1,
            )
        else:
            set_rule_enabled(
                session,
                workspace.id,
                rule.id,
                False,
                expected_lock_version=1,
            )
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

    workspace_lock_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update workspaces")
    )
    rule_update_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update merchant_rules")
    )
    assert workspace_lock_index < rule_update_index


@pytest.mark.parametrize(
    ("filters", "field"),
    [
        (HistoryFilters(direction=True), "direction"),  # type: ignore[arg-type]
        (HistoryFilters(date_from=20260815), "date_from"),  # type: ignore[arg-type]
        (HistoryFilters(account_id=True), "account_id"),
        (HistoryFilters(date_from="2026-08-16", date_to="2026-08-15"), "date_to"),
    ],
)
def test_preview_rejects_malformed_filter_types_and_inverted_dates(
    session: Session,
    workspace: Workspace,
    filters: HistoryFilters,
    field: str,
) -> None:
    """Break if bool/coerced filter values or an inverted range become a valid selection."""
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)

    with pytest.raises(RuleValidationError) as caught:
        preview_historical_application(
            session,
            workspace.id,
            rule.id,
            filters,
            user_id=workspace.owner_id,
            secret_key=SECRET,
        )

    assert field in caught.value.field_errors


def test_digest_includes_sorted_current_tags_and_source_link_state(
    session: Session, workspace: Workspace
) -> None:
    """Break if tag/source/link drift can reuse a preview or identical actions skip attribution."""
    target = _category(session, workspace.id, "Target")
    first_tag = _tag(session, workspace.id, "Alpha")
    second_tag = _tag(session, workspace.id, "Zulu")
    rule = _rule(
        session,
        workspace.id,
        target.id,
        tags=(second_tag, first_tag),
        normalized_merchant="Coffee Club",
    )
    transaction = _transaction(
        session,
        workspace.id,
        target.id,
        description="COFFEE TAGS",
        merchant="Coffee Club",
        tags=(first_tag, second_tag),
        source=CategorizationSource.UNCATEGORIZED.value,
        merchant_rule_id=None,
    )
    session.commit()
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    assert preview.would_change_count == 1
    assert preview.unchanged_count == 0
    transaction.tags = [first_tag]
    session.flush()

    with pytest.raises(StaleRuleApplicationError):
        confirm_historical_application(
            session,
            workspace.id,
            preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )


def test_confirmed_digest_match_also_requires_the_exact_canonical_selection(
    session: Session, workspace: Workspace
) -> None:
    """Break if a non-unique digest alone can return another selection's stored outcome."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    transaction = _transaction(
        session,
        workspace.id,
        current.id,
        description="COFFEE DIGEST COLLISION",
    )
    session.commit()
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=(transaction.id,),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    different_payload = ApplicationTokenPayload(
        workspace_id=workspace.id,
        merchant_rule_id=rule.id,
        rule_lock_version=rule.lock_version,
        selected_transaction_ids=(),
        state_digest=preview.state_digest,
        normalized_filters=preview.normalized_filters,
    )
    unrelated = RuleApplicationRun(
        workspace_id=workspace.id,
        merchant_rule_id=rule.id,
        initiated_by_user_id=workspace.owner_id,
        rule_name_snapshot=rule.name,
        rule_lock_version=rule.lock_version,
        status="confirmed",
        selection_json=canonical_application_selection(different_payload),
        preview_digest=preview.state_digest,
        matched_count=99,
        changed_count=99,
        unchanged_count=0,
        manual_skip_count=0,
        conflict_skip_count=0,
        confirmed_at=datetime.now(UTC),
    )
    session.add(unrelated)
    session.flush()

    result = confirm_historical_application(
        session,
        workspace.id,
        preview.token,
        workspace.owner_id,
        secret_key=SECRET,
    )

    assert result.run_id == preview.run_id
    assert result.changed_count == 1
    session.refresh(transaction)
    assert transaction.category_id == target.id


def test_preview_pages_all_matches_but_selects_at_most_five_hundred(
    session: Session, workspace: Workspace
) -> None:
    """Break if paging truncates counts or a default bounded selection exceeds 500."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    for index in range(505):
        _transaction(
            session,
            workspace.id,
            current.id,
            description=f"COFFEE BULK {index}",
        )
    session.commit()

    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )

    assert preview.matched_count == 505
    assert preview.would_change_count == 505
    assert len(preview.eligible_transaction_ids) == 500
    assert len(preview.selected_transaction_ids) == 500
    assert preview.selected_transaction_ids == preview.eligible_transaction_ids


def test_audit_failure_rolls_back_every_transaction_change(
    session: Session, workspace: Workspace
) -> None:
    """Break if transaction actions can persist independently of the audit confirmation."""
    current = _category(session, workspace.id, "Current")
    target = _category(session, workspace.id, "Target")
    rule = _rule(session, workspace.id, target.id)
    transactions = tuple(
        _transaction(
            session,
            workspace.id,
            current.id,
            description=f"COFFEE ATOMIC {index}",
        )
        for index in range(2)
    )
    session.commit()
    preview = preview_historical_application(
        session,
        workspace.id,
        rule.id,
        HistoryFilters(),
        selected_transaction_ids=tuple(item.id for item in transactions),
        user_id=workspace.owner_id,
        secret_key=SECRET,
    )
    session.commit()
    session.execute(
        text(
            "CREATE TRIGGER fail_history_audit BEFORE UPDATE OF status "
            "ON rule_application_runs WHEN NEW.status = 'confirmed' "
            "BEGIN SELECT RAISE(FAIL, 'audit failure'); END"
        )
    )
    session.commit()

    with pytest.raises(DBAPIError):
        confirm_historical_application(
            session,
            workspace.id,
            preview.token,
            workspace.owner_id,
            secret_key=SECRET,
        )
    assert session.is_active
    session.expire_all()

    assert [session.get(Transaction, item.id).category_id for item in transactions] == [
        current.id,
        current.id,
    ]
    run = session.get(RuleApplicationRun, preview.run_id)
    assert run is not None
    assert run.status == "previewed"
    assert run.confirmed_at is None


def _concurrent_history_store(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-history.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as seed:
        owner = User(
            google_sub="history-concurrent-owner",
            email="history-concurrent@example.com",
            display_name="Owner",
        )
        workspace = Workspace(name="Concurrent", is_personal=True, owner=owner)
        seed.add(workspace)
        seed.flush()
        seed.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=owner.id,
                role="member",
            )
        )
        current = _category(seed, workspace.id, "Current")
        target = _category(seed, workspace.id, "Target")
        rule = _rule(seed, workspace.id, target.id)
        transactions = tuple(
            _transaction(
                seed,
                workspace.id,
                current.id,
                description=f"COFFEE CONCURRENT {index}",
            )
            for index in range(2)
        )
        seed.commit()
        return (
            engine,
            factory,
            workspace.id,
            owner.id,
            rule.id,
            target.id,
            tuple(item.id for item in transactions),
        )


def test_concurrent_confirmation_serializes_nonunique_digest_and_reuses_outcome(
    tmp_path: Path,
) -> None:
    """Break if two sessions can both apply and confirm the same preview digest."""
    engine, factory, workspace_id, owner_id, rule_id, target_id, transaction_ids = (
        _concurrent_history_store(tmp_path)
    )
    try:
        with factory() as preview_session:
            first_preview = preview_historical_application(
                preview_session,
                workspace_id,
                rule_id,
                HistoryFilters(),
                selected_transaction_ids=transaction_ids,
                user_id=owner_id,
                secret_key=SECRET,
            )
            second_preview = preview_historical_application(
                preview_session,
                workspace_id,
                rule_id,
                HistoryFilters(),
                selected_transaction_ids=tuple(reversed(transaction_ids)),
                user_id=owner_id,
                secret_key=SECRET,
            )
            assert first_preview.state_digest == second_preview.state_digest
            preview_session.commit()

        barrier = Barrier(2)

        def confirm(token: str):
            with factory() as worker:
                barrier.wait(timeout=5)
                result = confirm_historical_application(
                    worker,
                    workspace_id,
                    token,
                    owner_id,
                    secret_key=SECRET,
                )
                worker.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(confirm, first_preview.token),
                pool.submit(confirm, second_preview.token),
            ]
            results = [future.result(timeout=20) for future in futures]

        assert results[0].run_id == results[1].run_id
        assert results[0].changed_count == results[1].changed_count == 2
        with factory() as check:
            runs = tuple(check.scalars(select(RuleApplicationRun).order_by(RuleApplicationRun.id)))
            assert sum(run.status == "confirmed" for run in runs) == 1
            assert [check.get(Transaction, item).category_id for item in transaction_ids] == [
                target_id,
                target_id,
            ]
    finally:
        engine.dispose()


def test_concurrent_confirmation_and_legacy_upsert_have_one_serial_order(
    tmp_path: Path,
) -> None:
    """Break if the legacy exact-key writer can change a rule during confirmation."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-legacy-writer.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as seed:
            owner = User(
                google_sub="history-legacy-writer-owner",
                email="history-legacy-writer@example.com",
                display_name="Owner",
            )
            workspace = Workspace(name="Concurrent writer", is_personal=True, owner=owner)
            seed.add(workspace)
            seed.flush()
            seed.add(
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    role="member",
                )
            )
            current = _category(seed, workspace.id, "Current")
            original_target = _category(seed, workspace.id, "Original target")
            replacement_target = _category(seed, workspace.id, "Replacement target")
            rule = _rule(seed, workspace.id, original_target.id)
            rule.merchant_pattern = "COFFEE CONCURRENT WRITER"
            transaction = _transaction(
                seed,
                workspace.id,
                current.id,
                description="COFFEE CONCURRENT WRITER",
            )
            seed.commit()
            workspace_id = workspace.id
            owner_id = owner.id
            rule_id = rule.id
            transaction_id = transaction.id
            current_category_id = current.id
            original_target_id = original_target.id
            replacement_target_id = replacement_target.id

        with factory() as preview_session:
            preview = preview_historical_application(
                preview_session,
                workspace_id,
                rule_id,
                HistoryFilters(),
                selected_transaction_ids=(transaction_id,),
                user_id=owner_id,
                secret_key=SECRET,
            )
            preview_session.commit()

        barrier = Barrier(2)

        def confirm() -> str:
            with factory() as worker:
                barrier.wait(timeout=5)
                try:
                    confirm_historical_application(
                        worker,
                        workspace_id,
                        preview.token,
                        owner_id,
                        secret_key=SECRET,
                    )
                except StaleRuleApplicationError as error:
                    assert error.run_id == preview.run_id
                    assert error.status == "stale"
                    worker.commit()
                    return "stale"
                worker.commit()
                return "confirmed"

        def update_legacy_rule() -> int:
            with factory() as worker:
                barrier.wait(timeout=5)
                updated = upsert_workspace_rule(
                    worker,
                    workspace_id,
                    "COFFEE CONCURRENT WRITER",
                    "Replacement merchant",
                    replacement_target_id,
                    False,
                )
                worker.commit()
                return updated.lock_version

        with ThreadPoolExecutor(max_workers=2) as pool:
            confirm_future = pool.submit(confirm)
            update_future = pool.submit(update_legacy_rule)
            outcome = confirm_future.result(timeout=20)
            updated_version = update_future.result(timeout=20)

        assert updated_version == 2
        with factory() as check:
            stored_rule = check.get(MerchantRule, rule_id)
            stored_transaction = check.get(Transaction, transaction_id)
            run = check.get(RuleApplicationRun, preview.run_id)
            assert stored_rule is not None
            assert stored_rule.category_id == replacement_target_id
            assert stored_rule.lock_version == 2
            assert stored_transaction is not None
            assert run is not None
            if outcome == "confirmed":
                assert stored_transaction.category_id == original_target_id
                assert run.status == "confirmed"
            else:
                assert stored_transaction.category_id == current_category_id
                assert run.status == "stale"
    finally:
        engine.dispose()
