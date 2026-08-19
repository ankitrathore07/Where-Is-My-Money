from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.models import MerchantRule, Tag, Transaction, Workspace
from app.tags.service import (
    BuiltinTagMutationError,
    DuplicateTagNameError,
    TagNotFoundError,
    create_custom_tag,
    delete_custom_tag,
    list_accessible_tags,
    rename_custom_tag,
    replace_rule_tags,
    replace_transaction_tags,
)


def _tag(session: Session, name: str, workspace_id: int | None = None) -> Tag:
    tag = Tag(workspace_id=workspace_id, name=name)
    session.add(tag)
    session.flush()
    return tag


def test_accessible_tags_include_builtins_and_only_active_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    builtin = _tag(session, "Household Expenditure")
    mine = _tag(session, "Renovation", workspace.id)
    _tag(session, "Private", other_workspace.id)

    choices = list_accessible_tags(session, workspace.id)

    assert choices.builtin == (builtin,)
    assert choices.workspace == (mine,)


def test_custom_tags_can_be_created_and_renamed_with_normalized_uniqueness(
    session: Session, workspace: Workspace
) -> None:
    tag = create_custom_tag(session, workspace.id, "  Family   Priority  ")

    assert (tag.name, tag.name_key) == ("Family Priority", "family priority")
    assert rename_custom_tag(session, workspace.id, tag.id, "Long-term Family") is tag
    assert (tag.name, tag.name_key) == ("Long-term Family", "long-term family")

    create_custom_tag(session, workspace.id, "Existing")
    with pytest.raises(DuplicateTagNameError):
        rename_custom_tag(session, workspace.id, tag.id, " EXISTING ")


def test_custom_tag_cannot_shadow_a_builtin_tag(session: Session, workspace: Workspace) -> None:
    _tag(session, "Family Support")

    with pytest.raises(DuplicateTagNameError):
        create_custom_tag(session, workspace.id, " family support ")


def test_builtin_and_foreign_tags_cannot_be_mutated(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    builtin = _tag(session, "Essential")
    foreign = _tag(session, "Private", other_workspace.id)

    with pytest.raises(BuiltinTagMutationError):
        rename_custom_tag(session, workspace.id, builtin.id, "Changed")
    with pytest.raises(TagNotFoundError):
        rename_custom_tag(session, workspace.id, foreign.id, "Changed")
    with pytest.raises(TagNotFoundError):
        delete_custom_tag(session, workspace.id, foreign.id)


def test_deleting_custom_tag_removes_associations_not_transactions(
    session: Session, workspace: Workspace
) -> None:
    tag = create_custom_tag(session, workspace.id, "Temporary")
    transaction = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, 13, tzinfo=UTC),
        description="TEST",
        amount_cents=-100,
    )
    session.add(transaction)
    session.flush()
    replace_transaction_tags(session, workspace.id, transaction, (tag.id,))

    delete_custom_tag(session, workspace.id, tag.id)

    assert session.get(Transaction, transaction.id) is transaction
    assert transaction.tags == []
    assert session.get(Tag, tag.id) is None


def test_deleting_custom_tag_serializes_before_loading_rule_resources(
    session: Session, workspace: Workspace
) -> None:
    """Break if tag deletion can race historical confirmation after its rule plan is loaded."""
    tag = create_custom_tag(session, workspace.id, "Serialized deletion")
    session.commit()
    tag_id = tag.id
    workspace_id = workspace.id
    session.expunge_all()
    statements: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.casefold().split()))

    try:
        delete_custom_tag(session, workspace_id, tag_id)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture_statement)

    workspace_locks = [
        index
        for index, statement in enumerate(statements)
        if statement.startswith("update workspaces")
    ]
    assert workspace_locks, statements
    tag_lookup_index = next(
        index for index, statement in enumerate(statements) if statement.startswith("select tags")
    )
    assert workspace_locks[0] < tag_lookup_index


def test_tag_assignment_is_workspace_scoped_and_idempotent(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    builtin = _tag(session, "Vehicle")
    mine = _tag(session, "Household", workspace.id)
    foreign = _tag(session, "Private", other_workspace.id)
    transaction = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, 13, tzinfo=UTC),
        description="INSURANCE",
        amount_cents=-60000,
    )
    rule = MerchantRule(workspace_id=workspace.id, merchant_pattern="INSURANCE")
    session.add_all((transaction, rule))
    session.flush()

    replace_transaction_tags(session, workspace.id, transaction, (mine.id, builtin.id, mine.id))
    replace_rule_tags(session, workspace.id, rule, (builtin.id, mine.id))

    assert [tag.name for tag in transaction.tags] == ["Household", "Vehicle"]
    assert [tag.name for tag in rule.tags] == ["Household", "Vehicle"]
    with pytest.raises(TagNotFoundError):
        replace_transaction_tags(session, workspace.id, transaction, (foreign.id,))
    assert set(session.scalars(select(Tag).where(Tag.workspace_id == other_workspace.id))) == {
        foreign
    }
