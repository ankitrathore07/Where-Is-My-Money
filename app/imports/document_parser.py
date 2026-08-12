import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.imports.types import CsvDocument, CsvSourceRow

DATE_AT_START = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\s+(?P<body>.+)$"
)
MONEY_TOKEN = re.compile(
    r"(?<![\w.])(?P<amount>\(?[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})\)?)"
    r"(?:\s*(?P<direction>CR|DR|CREDIT|DEBIT))?(?![\w.])",
    re.IGNORECASE,
)
MAX_TRANSACTIONS = 50_000


class TransactionStatementFormatError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _iso_date(value: str) -> str:
    if "-" in value:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise TransactionStatementFormatError(
                "invalid_transaction_date", "A transaction contains an invalid date."
            ) from exc
    month, day, year = value.split("/")
    if len(year) == 2:
        year = f"20{year}" if int(year) < 70 else f"19{year}"
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError as exc:
        raise TransactionStatementFormatError(
            "invalid_transaction_date", "A transaction contains an invalid date."
        ) from exc


def _signed_amount(match: re.Match[str]) -> str | None:
    raw = match.group("amount")
    direction = (match.group("direction") or "").casefold()
    accounting_negative = raw.startswith("(") and raw.endswith(")")
    normalized = raw.strip("()").replace("$", "").replace(",", "")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if amount == 0:
        return None
    if accounting_negative or normalized.startswith("-") or direction in {"dr", "debit"}:
        amount = -abs(amount)
    elif normalized.startswith("+") or direction in {"cr", "credit"}:
        amount = abs(amount)
    else:
        return None
    return f"{amount:.2f}"


def parse_transaction_statement_text(text: str) -> CsvDocument:
    """Extract explicitly signed transaction rows from local PDF/OCR text.

    Direction is never guessed: every parsed amount must have a sign, accounting
    parentheses, or an adjacent debit/credit marker. This keeps balances and
    unsigned statement totals from silently becoming transactions.
    """
    rows: list[CsvSourceRow] = []
    ambiguous_lines: list[int] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line_match = DATE_AT_START.match(" ".join(raw_line.split()))
        if line_match is None:
            continue
        body = line_match.group("body")
        money_matches = tuple(MONEY_TOKEN.finditer(body))
        if not money_matches:
            continue
        directional = tuple(
            (match, amount)
            for match in money_matches
            if (amount := _signed_amount(match)) is not None
        )
        if len(directional) != 1:
            ambiguous_lines.append(line_number)
            continue
        _, amount = directional[0]
        description_parts: list[str] = []
        previous_end = 0
        for match in money_matches:
            description_parts.append(body[previous_end : match.start()])
            previous_end = match.end()
        description_parts.append(body[previous_end:])
        description = " ".join(" ".join(description_parts).split())
        if not description or len(description) > 512:
            ambiguous_lines.append(line_number)
            continue
        rows.append(
            CsvSourceRow(
                line_number,
                {
                    "Date": _iso_date(line_match.group("date")),
                    "Description": description,
                    "Amount": amount,
                },
            )
        )
        if len(rows) > MAX_TRANSACTIONS:
            raise TransactionStatementFormatError(
                "too_many_transactions",
                f"Transaction statements may contain at most {MAX_TRANSACTIONS} transactions.",
            )

    if ambiguous_lines:
        raise TransactionStatementFormatError(
            "ambiguous_transaction_rows",
            "Some dated statement rows do not identify exactly one debit or credit. "
            "Use a statement whose transactions show a sign, parentheses, or a "
            "debit/credit marker.",
        )
    if not rows:
        raise TransactionStatementFormatError(
            "transactions_missing",
            "No unambiguous transactions were found in this statement PDF.",
        )
    return CsvDocument(headers=("Date", "Description", "Amount"), rows=tuple(rows), delimiter="pdf")
