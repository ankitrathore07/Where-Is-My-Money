from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.accounts.service import (
    AccountNotFoundError,
    AccountValidationError,
    add_manual_balance,
    create_account,
    get_workspace_account,
    list_workspace_accounts,
    update_account,
)
from app.accounts.types import ACCOUNT_TYPE_OPTIONS, AccountInput, ManualBalanceInput
from app.db.models import Account, AccountBalanceSnapshot, Workspace


def test_account_type_catalog_uses_schema_values_and_fixed_classifications() -> None:
    by_value = {option.value: option for option in ACCOUNT_TYPE_OPTIONS}
    assert tuple(by_value) == (
        "checking",
        "savings",
        "credit_card",
        "investment_401k",
        "investment_brokerage",
        "mortgage",
        "auto_loan",
        "student_loan",
        "other",
    )
    assert by_value["checking"].default_is_liability is False
    assert by_value["mortgage"].default_is_liability is True
    assert by_value["other"].default_is_liability is None


def test_create_account_normalizes_text_and_persists_integer_workspace_id(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(
        session,
        workspace.id,
        AccountInput("  Everyday   Checking  ", "checking", " Example  CU ", False),
    )
    session.commit()
    assert account.name == "Everyday Checking"
    assert account.institution == "Example CU"
    assert account.workspace_id == workspace.id
    assert account.is_liability is False


@pytest.mark.parametrize(
    ("values", "field"),
    [
        (AccountInput("   ", "checking", "", False), "name"),
        (AccountInput("x" * 256, "checking", "", False), "name"),
        (AccountInput("Card", "unknown", "", True), "account_type"),
        (AccountInput("Checking", "checking", "", True), "is_liability"),
        (AccountInput("Mortgage", "mortgage", "", False), "is_liability"),
        (AccountInput("Other", "other", "x" * 256, False), "institution"),
    ],
)
def test_invalid_account_input_reports_the_specific_field(
    session: Session, workspace: Workspace, values: AccountInput, field: str
) -> None:
    with pytest.raises(AccountValidationError) as error:
        create_account(session, workspace.id, values)
    assert field in error.value.field_errors
    assert session.query(Account).count() == 0


def test_update_normalizes_and_replaces_an_account_in_its_workspace(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(session, workspace.id, AccountInput("Old", "checking", "", False))

    updated = update_account(
        session,
        workspace.id,
        account.id,
        AccountInput("  New  Name ", "credit_card", " New  Bank ", True),
    )

    assert updated.id == account.id
    assert updated.name == "New Name"
    assert updated.account_type == "credit_card"
    assert updated.institution == "New Bank"
    assert updated.is_liability is True


def test_update_rejects_invalid_values_without_mutating_the_account(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(
        session, workspace.id, AccountInput("Checking", "checking", "Bank", False)
    )

    with pytest.raises(AccountValidationError):
        update_account(
            session,
            workspace.id,
            account.id,
            AccountInput("", "checking", "Changed Bank", False),
        )

    assert account.name == "Checking"
    assert account.institution == "Bank"


def test_update_and_get_never_cross_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    foreign = Account(
        workspace_id=other_workspace.id,
        name="SECRET Brokerage",
        account_type="investment_brokerage",
        is_liability=False,
    )
    session.add(foreign)
    session.commit()
    with pytest.raises(AccountNotFoundError):
        get_workspace_account(session, workspace.id, foreign.id)
    with pytest.raises(AccountNotFoundError):
        update_account(
            session,
            workspace.id,
            foreign.id,
            AccountInput("Changed", "investment_brokerage", "", False),
        )
    assert foreign.name == "SECRET Brokerage"


def test_list_workspace_accounts_excludes_other_workspaces_and_has_stable_ordering(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    first_alpha = create_account(
        session, workspace.id, AccountInput("alpha", "checking", "", False)
    )
    second_alpha = create_account(
        session, workspace.id, AccountInput("Alpha", "checking", "", False)
    )
    checking_z = create_account(session, workspace.id, AccountInput("Zebra", "checking", "", False))
    savings = create_account(session, workspace.id, AccountInput("Savings", "savings", "", False))
    create_account(session, other_workspace.id, AccountInput("Private", "checking", "", False))

    accounts = list_workspace_accounts(session, workspace.id)

    assert tuple(account.id for account in accounts) == (
        first_alpha.id,
        second_alpha.id,
        checking_z.id,
        savings.id,
    )


@pytest.mark.parametrize(
    ("amount", "field"),
    [
        ("", "amount"),
        ("-1.00", "amount"),
        ("1.001", "amount"),
        ("not-money", "amount"),
        ("1e2", "amount"),
        ("NaN", "amount"),
        ("Infinity", "amount"),
        ("90000000000000.01", "amount"),
    ],
)
def test_manual_balance_rejects_invalid_amounts(
    session: Session, workspace: Workspace, amount: str, field: str
) -> None:
    account = create_account(session, workspace.id, AccountInput("Savings", "savings", "", False))
    with pytest.raises(AccountValidationError) as error:
        add_manual_balance(
            session,
            workspace.id,
            account.id,
            ManualBalanceInput(amount, "2026-08-10"),
            today=date(2026, 8, 10),
        )
    assert field in error.value.field_errors


@pytest.mark.parametrize("as_of_date", ["", "20260810", "2026/08/10", "2026-08-10T00:00:00"])
def test_manual_balance_requires_an_iso_calendar_date(
    session: Session, workspace: Workspace, as_of_date: str
) -> None:
    account = create_account(session, workspace.id, AccountInput("Savings", "savings", "", False))

    with pytest.raises(AccountValidationError) as error:
        add_manual_balance(
            session,
            workspace.id,
            account.id,
            ManualBalanceInput("1.00", as_of_date),
            today=date(2026, 8, 10),
        )

    assert "as_of_date" in error.value.field_errors


def test_manual_balance_converts_exact_cents_and_rejects_future_date(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(session, workspace.id, AccountInput("Mortgage", "mortgage", "", True))
    snapshot = add_manual_balance(
        session,
        workspace.id,
        account.id,
        ManualBalanceInput("83130.45", "2026-08-10"),
        today=date(2026, 8, 10),
    )
    assert snapshot.balance_cents == 8_313_045
    assert snapshot.workspace_id == workspace.id
    assert snapshot.source == "manual"
    with pytest.raises(AccountValidationError) as error:
        add_manual_balance(
            session,
            workspace.id,
            account.id,
            ManualBalanceInput("1.00", "2026-08-11"),
            today=date(2026, 8, 10),
        )
    assert "as_of_date" in error.value.field_errors


def test_manual_balance_accepts_the_exact_maximum_amount(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(session, workspace.id, AccountInput("Savings", "savings", "", False))

    snapshot = add_manual_balance(
        session,
        workspace.id,
        account.id,
        ManualBalanceInput("90000000000000.00", "2026-08-10"),
        today=date(2026, 8, 10),
    )

    assert snapshot.balance_cents == 9_000_000_000_000_000


def test_manual_balance_cannot_target_an_account_in_another_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    foreign = create_account(
        session,
        other_workspace.id,
        AccountInput("Private", "savings", "", False),
    )

    with pytest.raises(AccountNotFoundError):
        add_manual_balance(
            session,
            workspace.id,
            foreign.id,
            ManualBalanceInput("1.00", "2026-08-10"),
            today=date(2026, 8, 10),
        )

    assert session.query(AccountBalanceSnapshot).count() == 0
