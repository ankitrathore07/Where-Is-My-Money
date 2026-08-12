import csv
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from app.accounts.service import MAX_BALANCE_CENTS
from app.statement_imports.types import (
    StatementCandidate,
    StatementFormatError,
    StatementReviewValidationError,
    StatementReviewValues,
)

CSV_HEADER = (
    "account_name",
    "institution",
    "account_last_four",
    "total_balance",
    "as_of_date",
)
MONEY_PATTERN = re.compile(r"\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?")


def normalize_text(value: str) -> str:
    return " ".join(value.split())


def parse_money(value: str) -> int:
    normalized = value.strip()
    if not MONEY_PATTERN.fullmatch(normalized):
        raise StatementFormatError(
            "invalid_balance", "Enter a non-negative total with at most two decimals."
        )
    try:
        cents = int(Decimal(normalized.replace("$", "").replace(",", "")) * 100)
    except InvalidOperation:
        raise StatementFormatError("invalid_balance", "The total balance is invalid.") from None
    if cents > MAX_BALANCE_CENTS:
        raise StatementFormatError("invalid_balance", "The total balance is too large.")
    return cents


def parse_document_date(value: str) -> date:
    normalized = normalize_text(value)
    for format_string in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(normalized, format_string).date()
        except ValueError:
            continue
    raise StatementFormatError("invalid_date", "The statement date is invalid.")


def parse_wimm_csv(data: bytes) -> StatementCandidate:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise StatementFormatError("invalid_csv_encoding", "Use a UTF-8 balance CSV.") from None
    reader = csv.reader(StringIO(text, newline=""))
    rows = list(reader)
    if not rows or tuple(rows[0]) != CSV_HEADER:
        raise StatementFormatError("invalid_csv_header", "Use the documented balance CSV header.")
    if len(rows) != 2 or len(rows[1]) != len(CSV_HEADER):
        raise StatementFormatError("invalid_csv_rows", "Include exactly one balance data row.")
    if any(value.lstrip().startswith("=") for value in rows[1]):
        raise StatementFormatError(
            "invalid_csv_formula", "Balance CSV values must not contain formulas."
        )
    values = dict(zip(CSV_HEADER, rows[1], strict=True))
    account_name = normalize_text(values["account_name"])
    institution = normalize_text(values["institution"]) or None
    last_four = values["account_last_four"].strip() or None
    if not account_name or len(account_name) > 255 or (institution and len(institution) > 255):
        raise StatementFormatError("missing_account_identity", "Enter a valid account name.")
    if last_four is not None and not re.fullmatch(r"[0-9]{4}", last_four, re.ASCII):
        raise StatementFormatError("invalid_account_last_four", "Use exactly four digits.")
    raw_date = values["as_of_date"].strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date, re.ASCII):
        raise StatementFormatError("invalid_date", "Use an ISO YYYY-MM-DD date.")
    try:
        as_of_date = date.fromisoformat(raw_date)
    except ValueError:
        raise StatementFormatError("invalid_date", "Use an ISO YYYY-MM-DD date.") from None
    return StatementCandidate(
        account_name=account_name,
        institution=institution,
        account_last_four=last_four,
        balance_cents=parse_money(values["total_balance"]),
        as_of_date=as_of_date,
        extraction_method="wimm_csv",
    )


def validate_statement_review(form: Mapping[str, str], *, today: date) -> StatementReviewValues:
    errors: dict[str, str] = {}
    try:
        account_id = int(str(form.get("account_id", "")))
        if account_id <= 0:
            raise ValueError
    except ValueError:
        errors["account_id"] = "Choose an account."
        account_id = 0

    account_name = normalize_text(str(form.get("account_name", "")))
    if not account_name or len(account_name) > 255:
        errors["account_name"] = "Enter an account identity using 255 characters or fewer."
    institution_text = normalize_text(str(form.get("institution", "")))
    institution = institution_text or None
    if institution is not None and len(institution) > 255:
        errors["institution"] = "Use 255 characters or fewer."
    account_last_four_text = str(form.get("account_last_four", "")).strip()
    account_last_four = account_last_four_text or None
    if account_last_four is not None and not re.fullmatch(r"[0-9]{4}", account_last_four, re.ASCII):
        errors["account_last_four"] = "Use exactly four digits or leave this blank."

    try:
        balance_cents = parse_money(str(form.get("total_balance", "")))
    except StatementFormatError:
        errors["total_balance"] = "Enter a non-negative amount with at most two decimals."
        balance_cents = 0
    raw_date = str(form.get("as_of_date", "")).strip()
    try:
        as_of_date = date.fromisoformat(raw_date)
        if as_of_date > today:
            raise ValueError
    except ValueError:
        errors["as_of_date"] = "Enter a valid date that is not in the future."
        as_of_date = today

    if errors:
        raise StatementReviewValidationError(errors)
    return StatementReviewValues(
        account_id=account_id,
        account_name=account_name,
        institution=institution,
        account_last_four=account_last_four,
        balance_cents=balance_cents,
        as_of_date=as_of_date,
    )
