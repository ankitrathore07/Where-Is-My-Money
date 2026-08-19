from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event, current_thread, main_thread

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.rules.service as rule_service
from app.db.models import (
    Account,
    Base,
    Category,
    MerchantRule,
    Tag,
    Transaction,
    User,
    Workspace,
)
from app.rules.service import (
    RuleConflictError,
    RuleDraft,
    RuleNotFoundError,
    RuleResourceNotFoundError,
    RuleValidationError,
    create_rule,
    delete_rule,
    duplicate_rule,
    get_rule,
    list_rules,
    move_rule,
    set_rule_enabled,
    update_rule,
)
from app.rules.types import PredicateCondition


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


def _account(session: Session, workspace_id: int, name: str = "Checking") -> Account:
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type="checking",
        institution="Bank",
        is_liability=False,
    )
    session.add(account)
    session.flush()
    return account


def _draft(
    category_id: int,
    *,
    name: str = "Netflix purchases",
    condition: PredicateCondition | None = None,
    normalized_merchant: str | None = "Netflix",
    tag_ids: tuple[int, ...] = (),
    is_subscription: bool = True,
    billing_period_months: int | None = 1,
) -> RuleDraft:
    return RuleDraft(
        name=name,
        condition=condition or PredicateCondition("merchant_key", "exact", "NETFLIX COM"),
        normalized_merchant=normalized_merchant,
        category_id=category_id,
        tag_ids=tag_ids,
        is_subscription=is_subscription,
        billing_period_months=billing_period_months,
    )


def _seed_rule(
    session: Session,
    workspace_id: int,
    category_id: int,
    *,
    name: str,
    priority: int,
) -> MerchantRule:
    rule = MerchantRule(
        workspace_id=workspace_id,
        name=name,
        enabled=True,
        priority=priority,
        condition_version=1,
        condition_json={
            "version": 1,
            "type": "predicate",
            "field": "merchant_key",
            "operator": "exact",
            "value": name.upper(),
        },
        lock_version=1,
        normalized_merchant=name,
        category_id=category_id,
        is_subscription=False,
    )
    session.add(rule)
    session.flush()
    return rule


def _concurrent_store(tmp_path: Path, *, rule_count: int = 0):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-rules.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as seed:
        owner = User(google_sub="concurrent-owner", email="concurrent@example.com")
        workspace = Workspace(name="Concurrent", is_personal=True, owner=owner)
        category = Category(
            workspace=None,
            name="Concurrent category",
            name_key="concurrent category",
            kind="expense",
        )
        seed.add_all((workspace, category))
        seed.flush()
        rules = [
            _seed_rule(
                seed,
                workspace.id,
                category.id,
                name=f"Concurrent {index}",
                priority=index,
            )
            for index in range(rule_count)
        ]
        seed.commit()
        return engine, factory, workspace.id, category.id, tuple(rule.id for rule in rules)


def _race_real_calls(monkeypatch: pytest.MonkeyPatch, function_name: str) -> Event:
    """Make two worker sessions finish the same real pre-write read before continuing."""
    real_function = getattr(rule_service, function_name)
    barrier = Barrier(2)
    raced = Event()

    def synchronized(*args, **kwargs):
        result = real_function(*args, **kwargs)
        if current_thread() is not main_thread():
            try:
                barrier.wait(timeout=0.3)
                raced.set()
            except BrokenBarrierError:
                pass
        return result

    monkeypatch.setattr(rule_service, function_name, synchronized)
    return raced


def test_create_rule_validates_and_flushes_typed_draft_at_end_of_workspace(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Streaming")
    builtin_tag = _tag(session, None, "Subscription")
    custom_tag = _tag(session, workspace.id, "Household")
    _seed_rule(session, workspace.id, category.id, name="Earlier", priority=7)

    rule = create_rule(
        session,
        workspace.id,
        _draft(
            category.id,
            name="  Netflix   purchases  ",
            normalized_merchant="  Netflix   US ",
            tag_ids=(custom_tag.id, builtin_tag.id, custom_tag.id),
        ),
    )

    assert rule.id is not None
    assert rule.name == "Netflix purchases"
    assert rule.enabled is True
    assert rule.priority == 1
    assert rule.lock_version == 1
    assert rule.merchant_pattern is None
    assert rule.condition_json == {
        "field": "merchant_key",
        "operator": "exact",
        "type": "predicate",
        "value": "NETFLIX COM",
        "version": 1,
    }
    assert rule.normalized_merchant == "Netflix US"
    assert rule.category_id == category.id
    assert [tag.id for tag in rule.tags] == [custom_tag.id, builtin_tag.id]
    assert session.get(MerchantRule, rule.id) is rule


def test_create_rule_does_not_commit_the_callers_transaction(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Rollback")
    session.commit()

    rule = create_rule(session, workspace.id, _draft(category.id))
    rule_id = rule.id
    session.rollback()

    assert session.get(MerchantRule, rule_id) is None


def test_concurrent_creates_in_empty_workspace_keep_dense_unique_priorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory, workspace_id, category_id, _rule_ids = _concurrent_store(tmp_path)
    _race_real_calls(monkeypatch, "list_rules")

    def create(name: str) -> None:
        with factory() as worker:
            create_rule(worker, workspace_id, _draft(category_id, name=name))
            worker.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create, name) for name in ("First", "Second")]
            for future in futures:
                future.result(timeout=10)

        with factory() as check:
            rules = list_rules(check, workspace_id)
            assert {rule.name for rule in rules} == {"First", "Second"}
            assert [rule.priority for rule in rules] == [0, 1]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "   "}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"normalized_merchant": "x" * 256}, "normalized_merchant"),
        ({"is_subscription": 1}, "is_subscription"),
        ({"billing_period_months": 0}, "billing_period_months"),
        ({"billing_period_months": 121}, "billing_period_months"),
        ({"tag_ids": (True,)}, "tag_ids"),
    ],
)
def test_create_rule_rejects_invalid_names_and_actions(
    session: Session,
    workspace: Workspace,
    changes: dict[str, object],
    field: str,
) -> None:
    category = _category(session, None, "Validation")
    values = _draft(category.id).__dict__ | changes

    with pytest.raises(RuleValidationError) as caught:
        create_rule(session, workspace.id, RuleDraft(**values))

    assert field in caught.value.field_errors
    assert session.scalar(select(MerchantRule)) is None


def test_create_rule_rejects_foreign_action_resources(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, other_workspace.id, "Foreign")
    tag = _tag(session, other_workspace.id, "Foreign")

    with pytest.raises(RuleResourceNotFoundError):
        create_rule(session, workspace.id, _draft(category.id, tag_ids=(tag.id,)))

    assert session.scalar(select(MerchantRule)) is None


def test_create_rule_rejects_foreign_account_condition(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, None, "Account filter")
    foreign = _account(session, other_workspace.id)

    with pytest.raises(RuleResourceNotFoundError):
        create_rule(
            session,
            workspace.id,
            _draft(category.id, condition=PredicateCondition("account_id", "equal", foreign.id)),
        )


def test_create_rule_rejects_unknown_provider_condition(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Provider filter")

    with pytest.raises(RuleValidationError) as caught:
        create_rule(
            session,
            workspace.id,
            _draft(
                category.id,
                condition=PredicateCondition("provider_key", "equal", "unknown_provider"),
            ),
        )

    assert "condition" in caught.value.field_errors


def test_get_and_list_rules_are_workspace_scoped(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, None, "Scoped")
    local = _seed_rule(session, workspace.id, category.id, name="Local", priority=3)
    _seed_rule(session, other_workspace.id, category.id, name="Foreign", priority=0)

    assert get_rule(session, workspace.id, local.id) is local
    assert list_rules(session, workspace.id) == (local,)
    with pytest.raises(RuleNotFoundError):
        get_rule(session, workspace.id, local.id + 1)


def test_update_rule_rejects_stale_lock_version_without_mutation(
    session: Session, workspace: Workspace
) -> None:
    current_category = _category(session, None, "Streaming")
    stale_category = _category(session, workspace.id, "Stale")
    current_tag = _tag(session, workspace.id, "Current")
    rule = _seed_rule(session, workspace.id, current_category.id, name="CURRENT", priority=0)
    rule.lock_version = 2
    rule.normalized_merchant = "Current merchant"
    rule.is_subscription = False
    rule.billing_period_months = None
    rule.tags = [current_tag]
    session.flush()

    with pytest.raises(RuleConflictError):
        update_rule(
            session,
            workspace.id,
            rule.id,
            _draft(stale_category.id, name="Stale", normalized_merchant="Stale merchant"),
            expected_lock_version=1,
        )

    session.refresh(rule)
    assert rule.name == "CURRENT"
    assert rule.lock_version == 2
    assert rule.normalized_merchant == "Current merchant"
    assert rule.category_id == current_category.id
    assert rule.is_subscription is False
    assert rule.billing_period_months is None
    assert [tag.id for tag in rule.tags] == [current_tag.id]


def test_update_rule_rejects_malformed_lock_version(session: Session, workspace: Workspace) -> None:
    category = _category(session, None, "Malformed lock")
    rule = _seed_rule(session, workspace.id, category.id, name="UNCHANGED", priority=0)

    with pytest.raises(RuleConflictError):
        update_rule(
            session,
            workspace.id,
            rule.id,
            _draft(category.id, name="Changed"),
            expected_lock_version=0,
        )

    session.refresh(rule)
    assert rule.name == "UNCHANGED"
    assert rule.lock_version == 1


def test_update_rule_replaces_actions_and_increments_lock_version(
    session: Session, workspace: Workspace
) -> None:
    old_category = _category(session, None, "Old")
    new_category = _category(session, workspace.id, "New")
    tag = _tag(session, workspace.id, "Needs review")
    rule = _seed_rule(session, workspace.id, old_category.id, name="Original", priority=0)

    updated = update_rule(
        session,
        workspace.id,
        rule.id,
        _draft(
            new_category.id,
            name="Updated",
            normalized_merchant=None,
            tag_ids=(tag.id,),
            is_subscription=False,
            billing_period_months=None,
        ),
        expected_lock_version=1,
    )

    assert updated is rule
    assert rule.name == "Updated"
    assert rule.category_id == new_category.id
    assert rule.normalized_merchant is None
    assert rule.is_subscription is False
    assert rule.billing_period_months is None
    assert [item.id for item in rule.tags] == [tag.id]
    assert rule.lock_version == 2
    assert rule.priority == 0


def test_set_rule_enabled_uses_workspace_and_optimistic_lock(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, None, "Toggle")
    rule = _seed_rule(session, workspace.id, category.id, name="Toggle", priority=0)

    disabled = set_rule_enabled(
        session,
        workspace.id,
        rule.id,
        False,
        expected_lock_version=1,
    )

    assert disabled.enabled is False
    assert disabled.lock_version == 2
    with pytest.raises(RuleConflictError):
        set_rule_enabled(
            session,
            workspace.id,
            rule.id,
            True,
            expected_lock_version=1,
        )
    with pytest.raises(RuleNotFoundError):
        set_rule_enabled(session, other_workspace.id, rule.id, True, expected_lock_version=2)


def test_move_rule_compacts_workspace_priorities_atomically(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, None, "Move")
    rules = [
        _seed_rule(session, workspace.id, category.id, name=f"Rule {index}", priority=index * 3)
        for index in range(3)
    ]
    foreign = _seed_rule(session, other_workspace.id, category.id, name="Foreign", priority=8)
    session.commit()

    moved = move_rule(session, workspace.id, rules[2].id, new_index=0)

    assert moved is rules[2]
    assert [(rule.id, rule.priority) for rule in list_rules(session, workspace.id)] == [
        (rules[2].id, 0),
        (rules[0].id, 1),
        (rules[1].id, 2),
    ]
    assert moved.lock_version == 2
    assert foreign.priority == 8
    session.rollback()
    assert [(rule.id, rule.priority) for rule in list_rules(session, workspace.id)] == [
        (rules[0].id, 0),
        (rules[1].id, 3),
        (rules[2].id, 6),
    ]


def test_concurrent_moves_serialize_without_losing_either_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory, workspace_id, _category_id, rule_ids = _concurrent_store(
        tmp_path, rule_count=4
    )
    _race_real_calls(monkeypatch, "list_rules")

    def move(rule_id: int) -> None:
        with factory() as worker:
            move_rule(worker, workspace_id, rule_id, new_index=0)
            worker.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(move, rule_id) for rule_id in rule_ids[2:]]
            for future in futures:
                future.result(timeout=10)

        with factory() as check:
            rules = list_rules(check, workspace_id)
            assert {rule.id for rule in rules[:2]} == {rule_ids[2], rule_ids[3]}
            assert [rule.id for rule in rules[2:]] == [rule_ids[0], rule_ids[1]]
            assert [rule.priority for rule in rules] == [0, 1, 2, 3]
    finally:
        engine.dispose()


def test_move_rule_rejects_out_of_range_index_without_reordering(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Move validation")
    rules = [
        _seed_rule(session, workspace.id, category.id, name=f"Rule {index}", priority=index)
        for index in range(2)
    ]

    with pytest.raises(RuleValidationError) as caught:
        move_rule(session, workspace.id, rules[0].id, new_index=2)

    assert "new_index" in caught.value.field_errors
    assert [rule.priority for rule in rules] == [0, 1]


def test_duplicate_rule_copies_typed_rule_and_inserts_after_source(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Duplicate")
    tag = _tag(session, workspace.id, "Copied")
    first = _seed_rule(session, workspace.id, category.id, name="First", priority=0)
    source = create_rule(
        session,
        workspace.id,
        _draft(category.id, name="Original", tag_ids=(tag.id,)),
    )
    source.enabled = False
    session.flush()

    duplicate = duplicate_rule(session, workspace.id, source.id)

    assert duplicate.id not in {first.id, source.id}
    assert duplicate.name == "Original copy"
    assert duplicate.enabled is False
    assert duplicate.lock_version == 1
    assert duplicate.condition_json == source.condition_json
    assert duplicate.condition_json is not source.condition_json
    assert duplicate.category_id == source.category_id
    assert [item.id for item in duplicate.tags] == [tag.id]
    assert [(item.id, item.priority) for item in list_rules(session, workspace.id)] == [
        (first.id, 0),
        (source.id, 1),
        (duplicate.id, 2),
    ]


def test_duplicate_rule_revalidates_persisted_resources(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    foreign_category = _category(session, other_workspace.id, "Foreign duplicate")
    source = _seed_rule(
        session,
        workspace.id,
        foreign_category.id,
        name="Invalid source",
        priority=0,
    )

    with pytest.raises(RuleResourceNotFoundError):
        duplicate_rule(session, workspace.id, source.id)

    assert list_rules(session, workspace.id) == (source,)


def test_concurrent_duplicates_keep_dense_unique_priorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory, workspace_id, _category_id, rule_ids = _concurrent_store(
        tmp_path, rule_count=2
    )
    _race_real_calls(monkeypatch, "list_rules")

    def duplicate() -> None:
        with factory() as worker:
            duplicate_rule(worker, workspace_id, rule_ids[0])
            worker.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(duplicate) for _index in range(2)]
            for future in futures:
                future.result(timeout=10)

        with factory() as check:
            rules = list_rules(check, workspace_id)
            assert len(rules) == 4
            assert [rule.priority for rule in rules] == [0, 1, 2, 3]
            assert sum(rule.name == "Concurrent 0 copy" for rule in rules) == 2
    finally:
        engine.dispose()


def test_delete_rule_compacts_priorities_and_clears_transaction_attribution(
    session: Session, workspace: Workspace
) -> None:
    category = _category(session, None, "Delete")
    tag = _tag(session, workspace.id, "Preserved")
    first = _seed_rule(session, workspace.id, category.id, name="First", priority=0)
    deleted = _seed_rule(session, workspace.id, category.id, name="Deleted", priority=1)
    deleted.tags = [tag]
    last = _seed_rule(session, workspace.id, category.id, name="Last", priority=7)
    transaction = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, 15, tzinfo=UTC),
        description="Deleted",
        amount_cents=-100,
        normalized_merchant="Preserved merchant",
        category=category,
        is_subscription=True,
        billing_period_months=6,
        categorization_source="workspace_rule",
        merchant_rule=deleted,
        tags=[tag],
    )
    session.add(transaction)
    session.flush()
    transaction_id = transaction.id

    delete_rule(
        session,
        workspace.id,
        deleted.id,
        expected_lock_version=1,
    )
    session.commit()
    session.expire_all()
    reloaded = session.get(Transaction, transaction_id)

    assert session.get(MerchantRule, deleted.id) is None
    assert [(item.id, item.priority) for item in list_rules(session, workspace.id)] == [
        (first.id, 0),
        (last.id, 1),
    ]
    assert reloaded is not None
    assert reloaded.merchant_rule_id is None
    assert reloaded.merchant_rule is None
    assert reloaded.normalized_merchant == "Preserved merchant"
    assert reloaded.category_id == category.id
    assert reloaded.is_subscription is True
    assert reloaded.billing_period_months == 6
    assert reloaded.categorization_source == "workspace_rule"
    assert [item.id for item in reloaded.tags] == [tag.id]


def test_delete_rule_rejects_stale_or_foreign_rule(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    category = _category(session, None, "Delete conflict")
    rule = _seed_rule(session, workspace.id, category.id, name="Keep", priority=0)

    with pytest.raises(RuleConflictError):
        delete_rule(session, workspace.id, rule.id, expected_lock_version=2)
    with pytest.raises(RuleNotFoundError):
        delete_rule(session, other_workspace.id, rule.id, expected_lock_version=1)

    assert session.get(MerchantRule, rule.id) is rule


def test_concurrent_deletes_compact_without_losing_remaining_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory, workspace_id, _category_id, rule_ids = _concurrent_store(
        tmp_path, rule_count=4
    )
    concurrent_prewrite_reads = _race_real_calls(monkeypatch, "get_rule")

    def delete(rule_id: int) -> None:
        with factory() as worker:
            delete_rule(worker, workspace_id, rule_id, expected_lock_version=1)
            worker.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(delete, rule_id) for rule_id in rule_ids[1:3]]
            for future in futures:
                future.result(timeout=10)

        with factory() as check:
            rules = list_rules(check, workspace_id)
            assert [rule.id for rule in rules] == [rule_ids[0], rule_ids[3]]
            assert [rule.priority for rule in rules] == [0, 1]
            assert not concurrent_prewrite_reads.is_set()
    finally:
        engine.dispose()
