from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.db.models import Category, Transaction, Workspace
from app.transactions.queries import (
    FilterValidationError,
    list_transactions,
    parse_filters,
)


def _transaction(
    session: Session,
    workspace: Workspace,
    *,
    day: int,
    description: str,
    amount: int,
    category: Category | None = None,
    merchant: str | None = None,
    is_subscription: bool = False,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, day, 12, tzinfo=UTC),
        description=description,
        normalized_merchant=merchant,
        amount_cents=amount,
        category_id=category.id if category else None,
        categorization_source="uncategorized",
        is_subscription=is_subscription,
    )
    session.add(transaction)
    session.commit()
    return transaction


def test_parse_filters_sets_bounded_defaults() -> None:
    filters = parse_filters({})
    assert filters.start_date is None
    assert filters.end_date is None
    assert filters.category_id is None
    assert filters.direction == "all"
    assert filters.subscription == "all"
    assert filters.query == ""
    assert filters.page == 1
    assert filters.page_size == 50


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"start_date": "08/01/2026"}, "start_date"),
        ({"start_date": "2026-08-02", "end_date": "2026-08-01"}, "end_date"),
        ({"direction": "transfer"}, "direction"),
        ({"subscription": "sometimes"}, "subscription"),
        ({"page": "0"}, "page"),
        ({"page": "abc"}, "page"),
        ({"category_id": "abc"}, "category_id"),
        ({"q": "x" * 101}, "q"),
    ],
)
def test_invalid_filter_is_rejected(params: dict[str, str], field: str) -> None:
    with pytest.raises(FilterValidationError) as error:
        parse_filters(params)
    assert field in error.value.field_errors


def test_query_is_workspace_scoped_ordered_and_date_inclusive(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    _transaction(session, workspace, day=1, description="First", amount=-100)
    later_first = _transaction(session, workspace, day=2, description="Later A", amount=-200)
    later_second = _transaction(session, workspace, day=2, description="Later B", amount=300)
    _transaction(session, workspace, day=3, description="Last", amount=-400)
    _transaction(session, other_workspace, day=2, description="Private other", amount=-500)

    page = list_transactions(
        session,
        workspace.id,
        parse_filters({"start_date": "2026-08-02", "end_date": "2026-08-02"}),
    )

    assert [item.id for item in page.items] == [later_second.id, later_first.id]
    assert all(item.workspace_id == workspace.id for item in page.items)


@pytest.mark.parametrize(("direction", "expected"), [("expense", -125), ("income", 225)])
def test_direction_filter(
    session: Session, workspace: Workspace, direction: str, expected: int
) -> None:
    _transaction(session, workspace, day=1, description="Out", amount=-125)
    _transaction(session, workspace, day=2, description="In", amount=225)

    page = list_transactions(session, workspace.id, parse_filters({"direction": direction}))
    assert [item.amount_cents for item in page.items] == [expected]


@pytest.mark.parametrize(("subscription", "expected"), [("yes", "Subscribed"), ("no", "Ordinary")])
def test_subscription_filter_is_workspace_scoped(
    session: Session,
    workspace: Workspace,
    other_workspace: Workspace,
    subscription: str,
    expected: str,
) -> None:
    _transaction(
        session,
        workspace,
        day=1,
        description="Subscribed",
        amount=-100,
        is_subscription=True,
    )
    _transaction(session, workspace, day=2, description="Ordinary", amount=-200)
    _transaction(
        session,
        other_workspace,
        day=3,
        description="Private subscription",
        amount=-300,
        is_subscription=True,
    )

    page = list_transactions(session, workspace.id, parse_filters({"subscription": subscription}))

    assert [item.description for item in page.items] == [expected]


def test_category_filter_accepts_global_and_owned_but_rejects_foreign(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    global_category = Category(name="Groceries", kind="expense")
    owned = Category(workspace_id=workspace.id, name="Owned", kind="expense")
    foreign = Category(workspace_id=other_workspace.id, name="Secret", kind="expense")
    session.add_all([global_category, owned, foreign])
    session.commit()
    _transaction(
        session, workspace, day=1, description="Global", amount=-100, category=global_category
    )
    _transaction(session, workspace, day=2, description="Owned", amount=-200, category=owned)

    assert (
        list_transactions(
            session, workspace.id, parse_filters({"category_id": str(global_category.id)})
        ).total_items
        == 1
    )
    assert (
        list_transactions(
            session, workspace.id, parse_filters({"category_id": str(owned.id)})
        ).total_items
        == 1
    )
    with pytest.raises(FilterValidationError) as error:
        list_transactions(session, workspace.id, parse_filters({"category_id": str(foreign.id)}))
    assert error.value.field_errors == {"category_id": "Choose an available category."}
    assert "Secret" not in str(error.value)


def test_search_matches_description_or_merchant_and_escapes_wildcards(
    session: Session, workspace: Workspace
) -> None:
    _transaction(session, workspace, day=1, description="Example Market", amount=-100)
    _transaction(
        session,
        workspace,
        day=2,
        description="Card purchase",
        merchant="CORNER STORE",
        amount=-200,
    )
    literal = _transaction(session, workspace, day=3, description="Fee 10%_APR", amount=-300)

    assert (
        list_transactions(session, workspace.id, parse_filters({"q": "  MARKET "})).total_items == 1
    )
    assert list_transactions(session, workspace.id, parse_filters({"q": "corner"})).total_items == 1
    result = list_transactions(session, workspace.id, parse_filters({"q": "%_"}))
    assert [item.id for item in result.items] == [literal.id]


def test_pagination_uses_fifty_rows_and_reports_totals(
    session: Session, workspace: Workspace
) -> None:
    for index in range(55):
        _transaction(
            session,
            workspace,
            day=(index % 28) + 1,
            description=f"Row {index}",
            amount=-index - 1,
        )

    page = list_transactions(session, workspace.id, parse_filters({"page": "2"}))
    assert len(page.items) == 5
    assert page.total_items == 55
    assert page.page == 2
    assert page.total_pages == 2
