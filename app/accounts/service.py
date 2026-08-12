from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts.types import ACCOUNT_TYPE_OPTIONS, AccountInput, ManualBalanceInput
from app.db.models import Account, AccountBalanceSnapshot

MAX_BALANCE_CENTS = 9_000_000_000_000_000
_MAX_ACCOUNT_TEXT_LENGTH = 255
_ACCOUNT_TYPES = {option.value: option for option in ACCOUNT_TYPE_OPTIONS}


class AccountValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the account details below.")
        self.field_errors = field_errors


class AccountNotFoundError(LookupError):
    pass


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _validated_account_values(values: AccountInput) -> tuple[str, str, str, bool]:
    name = _normalized_text(values.name)
    institution = _normalized_text(values.institution)
    field_errors: dict[str, str] = {}

    if not name:
        field_errors["name"] = "Account name is required."
    elif len(name) > _MAX_ACCOUNT_TEXT_LENGTH:
        field_errors["name"] = "Account name must be 255 characters or fewer."

    if len(institution) > _MAX_ACCOUNT_TEXT_LENGTH:
        field_errors["institution"] = "Institution must be 255 characters or fewer."

    option = _ACCOUNT_TYPES.get(values.account_type)
    if option is None:
        field_errors["account_type"] = "Choose an account type."
    elif (
        option.default_is_liability is not None
        and values.is_liability != option.default_is_liability
    ):
        field_errors["is_liability"] = "This account type has a fixed classification."

    if field_errors:
        raise AccountValidationError(field_errors)
    return name, values.account_type, institution, values.is_liability


def create_account(session: Session, workspace_id: int, values: AccountInput) -> Account:
    """Validate, flush, and return a new account belonging to one workspace."""
    name, account_type, institution, is_liability = _validated_account_values(values)
    account = Account(
        workspace_id=workspace_id,
        name=name,
        account_type=account_type,
        institution=institution,
        is_liability=is_liability,
    )
    session.add(account)
    session.flush()
    return account


def get_workspace_account(session: Session, workspace_id: int, account_id: int) -> Account:
    """Return an account only when it belongs to the active workspace."""
    account = session.scalar(
        select(Account).where(
            Account.id == account_id,
            Account.workspace_id == workspace_id,
        )
    )
    if account is None:
        raise AccountNotFoundError("Account not found.")
    return account


def update_account(
    session: Session, workspace_id: int, account_id: int, values: AccountInput
) -> Account:
    """Validate and replace mutable account details in the active workspace."""
    account = get_workspace_account(session, workspace_id, account_id)
    name, account_type, institution, is_liability = _validated_account_values(values)
    account.name = name
    account.account_type = account_type
    account.institution = institution
    account.is_liability = is_liability
    session.flush()
    return account


def list_workspace_accounts(session: Session, workspace_id: int) -> tuple[Account, ...]:
    """Return only a workspace's accounts in a deterministic order."""
    return tuple(
        session.scalars(
            select(Account)
            .where(Account.workspace_id == workspace_id)
            .order_by(Account.account_type, func.lower(Account.name), Account.id)
        )
    )


def _parse_balance_cents(amount: str) -> int:
    amount_text = amount.strip()
    if not amount_text or "e" in amount_text.lower():
        raise AccountValidationError({"amount": "Enter a dollar amount with at most two decimals."})
    try:
        decimal_amount = Decimal(amount_text)
    except InvalidOperation:
        raise AccountValidationError({"amount": "Enter a valid dollar amount."}) from None

    if (
        not decimal_amount.is_finite()
        or decimal_amount < 0
        or decimal_amount.as_tuple().exponent < -2
    ):
        raise AccountValidationError(
            {"amount": "Enter a non-negative amount with at most two decimals."}
        )

    maximum_amount = Decimal(MAX_BALANCE_CENTS) / Decimal(100)
    if decimal_amount > maximum_amount:
        raise AccountValidationError({"amount": "Amount is too large."})
    return int(decimal_amount * 100)


def _parse_as_of_date(as_of_date: str, today: date) -> date:
    if (
        len(as_of_date) != 10
        or as_of_date[4] != "-"
        or as_of_date[7] != "-"
        or not (as_of_date[:4] + as_of_date[5:7] + as_of_date[8:]).isdigit()
    ):
        raise AccountValidationError({"as_of_date": "Enter a date as YYYY-MM-DD."})
    try:
        parsed_date = date.fromisoformat(as_of_date)
    except ValueError:
        raise AccountValidationError({"as_of_date": "Enter a valid calendar date."}) from None
    if parsed_date > today:
        raise AccountValidationError({"as_of_date": "Balance dates cannot be in the future."})
    return parsed_date


def add_manual_balance(
    session: Session,
    workspace_id: int,
    account_id: int,
    values: ManualBalanceInput,
    *,
    today: date,
) -> AccountBalanceSnapshot:
    """Add one manually-entered balance snapshot to a workspace-owned account."""
    balance_cents = _parse_balance_cents(values.amount)
    as_of_date = _parse_as_of_date(values.as_of_date, today)
    account = get_workspace_account(session, workspace_id, account_id)
    snapshot = AccountBalanceSnapshot(
        workspace_id=account.workspace_id,
        account_id=account.id,
        balance_cents=balance_cents,
        as_of_date=as_of_date,
        source="manual",
    )
    session.add(snapshot)
    session.flush()
    return snapshot
