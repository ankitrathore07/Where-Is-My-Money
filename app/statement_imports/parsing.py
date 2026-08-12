import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from app.accounts.service import MAX_BALANCE_CENTS
from app.statement_imports.types import StatementCandidate, StatementFormatError

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
    values = dict(zip(CSV_HEADER, rows[1], strict=True))
    account_name = normalize_text(values["account_name"])
    institution = normalize_text(values["institution"]) or None
    last_four = values["account_last_four"].strip() or None
    if not account_name or len(account_name) > 255 or (institution and len(institution) > 255):
        raise StatementFormatError("missing_account_identity", "Enter a valid account name.")
    if last_four is not None and not re.fullmatch(r"[0-9]{4}", last_four, re.ASCII):
        raise StatementFormatError("invalid_account_last_four", "Use exactly four digits.")
    try:
        as_of_date = date.fromisoformat(values["as_of_date"].strip())
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
