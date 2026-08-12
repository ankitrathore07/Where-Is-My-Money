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
IDENTITY_LABELS = ("Account name", "Plan name", "Account number", "Account ending in")
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
    identity_values = _labeled_values(lines, IDENTITY_LABELS)
    identities: list[tuple[str, str | None]] = []
    for value in identity_values:
        digits = re.sub(r"\D", "", value)
        last_four = digits[-4:] if len(digits) >= 4 else None
        name = f"Account ending in {last_four}" if last_four else value
        identities.append((name, last_four))
    identity = _one(tuple(identities), "missing_account_identity", "ambiguous_identity")
    assert isinstance(identity, tuple)

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
        account_name=identity[0],
        institution=institution,
        account_last_four=identity[1],
        balance_cents=balance,
        as_of_date=as_of_date,
        extraction_method=method,
    )
