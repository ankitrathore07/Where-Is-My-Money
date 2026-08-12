import re

from app.statement_imports.parsing import normalize_text, parse_document_date, parse_money
from app.statement_imports.types import (
    StatementCandidate,
    StatementFormatError,
    compatible_account_types,
)

BALANCE_LABELS = {
    "investment_401k": (
        "Total account balance",
        "Total plan balance",
        "Ending account value",
        "Account value",
    ),
    "brokerage": (
        "Total account value",
        "Ending account value",
        "Net account value",
        "Portfolio value",
    ),
    "mortgage": (
        "Unpaid principal balance",
        "Current principal balance",
        "Remaining principal balance",
    ),
    "loan": (
        "Outstanding principal balance",
        "Current principal balance",
        "Remaining principal balance",
    ),
    "other": ("Total balance", "Ending balance", "Current balance"),
}
IDENTITY_NAME_LABELS = ("Account name", "Plan name")
IDENTITY_NUMBER_LABELS = ("Account number", "Account ending in")
INSTITUTION_LABELS = ("Institution", "Provider", "Servicer", "Issuer")
DATE_LABELS = ("As of date", "Statement date", "Period ending")


def _labeled_values(lines: tuple[str, ...], labels: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for line in lines:
        for label in labels:
            match = re.fullmatch(rf"{re.escape(label)}\s*:?\s+(.+)", line, re.IGNORECASE)
            if match:
                values.append(normalize_text(match.group(1)))
                break
    return tuple(values)


def _one(values: tuple[object, ...], missing_code: str, ambiguous_code: str) -> object:
    unique = tuple(dict.fromkeys(values))
    if not unique:
        raise StatementFormatError(missing_code, "The statement is missing a required value.")
    if len(unique) != 1:
        raise StatementFormatError(ambiguous_code, "The statement contains conflicting values.")
    return unique[0]


def process_statement_text(category: str, text: str, method: str) -> StatementCandidate:
    compatible_account_types(category)
    lines = tuple(normalize_text(line) for line in text.splitlines() if normalize_text(line))
    names = _labeled_values(lines, IDENTITY_NAME_LABELS)
    numbers = _labeled_values(lines, IDENTITY_NUMBER_LABELS)
    if not names and not numbers:
        raise StatementFormatError(
            "missing_account_identity", "The statement is missing an account identity."
        )
    account_name = None
    if names:
        account_name = _one(names, "missing_account_identity", "ambiguous_identity")
        assert isinstance(account_name, str)
    last_four_values: list[str] = []
    for value in numbers:
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 4:
            last_four_values.append(digits[-4:])
    last_four = None
    if numbers:
        if not last_four_values:
            raise StatementFormatError(
                "missing_account_identity", "The account number is not recognizable."
            )
        last_four = _one(tuple(last_four_values), "missing_account_identity", "ambiguous_identity")
        assert isinstance(last_four, str)
    if account_name is None:
        account_name = f"Account ending in {last_four}"

    institution_values = _labeled_values(lines, INSTITUTION_LABELS)
    institution = None
    if institution_values:
        institution = _one(institution_values, "missing_account_identity", "ambiguous_identity")
        assert isinstance(institution, str)

    date_values = tuple(parse_document_date(value) for value in _labeled_values(lines, DATE_LABELS))
    as_of_date = _one(date_values, "missing_date", "ambiguous_date")
    assert isinstance(as_of_date, type(parse_document_date("2026-01-01")))

    raw_balances = _labeled_values(lines, BALANCE_LABELS[category])
    balances: list[int] = []
    for value in raw_balances:
        try:
            balances.append(parse_money(value))
        except StatementFormatError as exc:
            raise StatementFormatError("invalid_balance", exc.message) from exc
    balance = _one(tuple(balances), "missing_balance", "ambiguous_balance")
    assert isinstance(balance, int)
    return StatementCandidate(
        account_name=account_name,
        institution=institution,
        account_last_four=last_four,
        balance_cents=balance,
        as_of_date=as_of_date,
        extraction_method=method,
    )
