from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import Account, Category, MerchantRule, Tag, Workspace
from app.rules.loader import load_compiled_rule_set
from app.rules.types import RuleContext


def _condition(field: str, value: object, operator: str = "exact") -> dict[str, object]:
    return {
        "version": 1,
        "type": "predicate",
        "field": field,
        "operator": operator,
        "value": value,
    }


def _context(
    description: str = "NETFLIX COM",
    *,
    account_id: int | None = None,
) -> RuleContext:
    return RuleContext(
        description=description,
        merchant_key=description,
        amount_cents=-1599,
        transaction_date=date(2026, 8, 15),
        direction="expense",
        account_id=account_id,
        provider_key="generic_csv",
    )


def _category(session: Session, workspace_id: int, name: str = "Streaming") -> Category:
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
    category_id: int,
    *,
    name: str,
    priority: int,
    enabled: bool = True,
    condition_json: dict[str, object] | None = None,
) -> MerchantRule:
    return MerchantRule(
        workspace_id=workspace_id,
        name=name,
        priority=priority,
        enabled=enabled,
        condition_json=condition_json or _condition("description", "NETFLIX COM"),
        normalized_merchant=name,
        category_id=category_id,
    )


@contextmanager
def _query_counter(engine: Engine):
    count = SimpleNamespace(value=0)

    def increment(*_args: object, **_kwargs: object) -> None:
        count.value += 1

    event.listen(engine, "before_cursor_execute", increment)
    try:
        yield count
    finally:
        event.remove(engine, "before_cursor_execute", increment)


def test_compiled_rule_set_uses_priority_then_id_and_ignores_disabled(
    session: Session, workspace: Workspace
) -> None:
    """Break if disabled rules run or ascending priority/ID ordering changes."""
    category = _category(session, workspace.id)
    session.add_all(
        [
            _rule(
                workspace.id,
                category.id,
                name="Disabled first",
                priority=0,
                enabled=False,
            ),
            _rule(
                workspace.id,
                category.id,
                name="Highest priority enabled",
                priority=1,
            ),
            _rule(workspace.id, category.id, name="Same priority later ID", priority=1),
            _rule(workspace.id, category.id, name="Lower priority", priority=2),
        ]
    )
    session.commit()

    match = load_compiled_rule_set(session, workspace.id).match(_context())

    assert match is not None
    assert match.rule.name == "Highest priority enabled"


def test_compiled_rule_set_rejects_foreign_categories_tags_and_accounts(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    """Break if any rule action or account predicate crosses its workspace boundary."""
    local_category = _category(session, workspace.id, "Local")
    foreign_category = _category(session, other_workspace.id, "Foreign")
    foreign_tag = Tag(workspace_id=other_workspace.id, name="Foreign tag")
    foreign_account = Account(
        workspace_id=other_workspace.id,
        name="Foreign account",
        account_type="checking",
        institution_key="other",
        institution="Elsewhere",
        is_liability=False,
    )
    session.add_all([foreign_tag, foreign_account])
    session.flush()
    category_rule = _rule(
        workspace.id,
        foreign_category.id,
        name="Foreign category",
        priority=0,
    )
    tag_rule = _rule(
        workspace.id,
        local_category.id,
        name="Foreign tag",
        priority=1,
    )
    tag_rule.tags = [foreign_tag]
    account_rule = _rule(
        workspace.id,
        local_category.id,
        name="Foreign account",
        priority=2,
        condition_json=_condition("account_id", foreign_account.id, "equal"),
    )
    session.add_all([category_rule, tag_rule, account_rule])
    session.commit()

    compiled = load_compiled_rule_set(session, workspace.id)

    assert compiled.match(_context(account_id=foreign_account.id)) is None
    assert [(item.rule_id, item.reason) for item in compiled.diagnostics] == [
        (category_rule.id, "inaccessible_category"),
        (tag_rule.id, "inaccessible_tag"),
        (account_rule.id, "inaccessible_account"),
    ]


def test_invalid_rule_fails_closed_with_value_free_diagnostic(
    session: Session, workspace: Workspace
) -> None:
    """Break if malformed persisted values match or appear in diagnostics."""
    category = _category(session, workspace.id)
    secret = "PRIVATE MERCHANT VALUE"
    rule = _rule(
        workspace.id,
        category.id,
        name="Malformed",
        priority=0,
        condition_json={**_condition("description", secret), "unexpected": secret},
    )
    session.add(rule)
    session.commit()

    compiled = load_compiled_rule_set(session, workspace.id)

    assert compiled.match(_context(secret)) is None
    assert [(item.rule_id, item.reason) for item in compiled.diagnostics] == [
        (rule.id, "invalid_condition")
    ]
    assert secret not in repr(compiled.diagnostics)


def test_loading_rule_set_query_count_is_constant(session: Session, workspace: Workspace) -> None:
    """Break if loading or matching adds queries per rule or transaction row."""
    category = _category(session, workspace.id)
    session.add_all(
        [
            _rule(
                workspace.id,
                category.id,
                name=f"Rule {index}",
                priority=index,
                condition_json=_condition("description", f"MERCHANT {index}"),
            )
            for index in range(30)
        ]
    )
    session.commit()
    assert session.bind is not None

    with _query_counter(session.bind) as count:
        compiled = load_compiled_rule_set(session, workspace.id)
        for index in range(200):
            compiled.match(_context(f"MERCHANT {index}"))

    assert count.value <= 4


def test_compiled_match_keeps_immutable_actions_without_session_queries(
    session: Session, workspace: Workspace
) -> None:
    """Break if a compiled action retains live ORM state or lazy-loads after expiration."""
    category = _category(session, workspace.id)
    tag = Tag(workspace_id=workspace.id, name="Streaming tag")
    rule = _rule(
        workspace.id,
        category.id,
        name="Stable action",
        priority=0,
    )
    rule.tags = [tag]
    session.add(rule)
    session.commit()
    compiled = load_compiled_rule_set(session, workspace.id)
    expected_rule_id = rule.id
    expected_tag_id = tag.id
    session.commit()
    assert session.bind is not None

    with _query_counter(session.bind) as count:
        match = compiled.match(_context())
        assert match is not None
        tag_ids = (
            match.rule.tag_ids
            if hasattr(match.rule, "tag_ids")
            else tuple(tag.id for tag in match.rule.tags)
        )
        action = (
            match.rule.id,
            match.rule.name,
            match.rule.normalized_merchant,
            match.rule.category_id,
            tag_ids,
        )

    assert action == (
        expected_rule_id,
        "Stable action",
        "Stable action",
        category.id,
        (expected_tag_id,),
    )
    assert count.value == 0
