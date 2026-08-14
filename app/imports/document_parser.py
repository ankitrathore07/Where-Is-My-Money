import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.imports.types import CsvDocument, CsvSourceRow

DATE_AT_START = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\s+(?P<body>.+)$"
)
YEARLESS_DATE_AT_START = re.compile(r"^\s*(?P<month>\d{1,2})/(?P<day>\d{1,2})\s+(?P<body>.+)$")
STATEMENT_PERIOD = re.compile(
    r"(?P<start>(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4})\s+"
    r"(?:through|to)\s+"
    r"(?P<end>(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)
MONEY_TOKEN = re.compile(
    r"(?<![\w.])(?:(?P<direction_before>CR|DR|CREDIT|DEBIT)\s+)?"
    r"(?P<amount>\(?[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})\)?)"
    r"(?:\s*(?P<direction_after>CR|DR|CREDIT|DEBIT))?(?![\w.])",
    re.IGNORECASE,
)
OPENING_BALANCE_LABEL = re.compile(r"\b(?:BEGINNING|OPENING)\s+BALANCE\b", re.IGNORECASE)
ASSET_STATEMENT_LABEL = re.compile(
    r"\b(?:CHECKING|SAVINGS)(?:\s+ACCOUNT)?(?:\s+STATEMENT)?\b", re.IGNORECASE
)
LIABILITY_STATEMENT_LABEL = re.compile(
    r"\bCREDIT[\s-]*CARD(?:\s+ACCOUNT)?(?:\s+STATEMENT)?\b", re.IGNORECASE
)
MAX_TRANSACTIONS = 50_000


class TransactionStatementFormatError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _ColumnTransaction:
    line_number: int
    date: str
    description: str
    amount_magnitude: Decimal | None
    explicit_amount: Decimal | None
    balance: Decimal


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


def _statement_period(text: str) -> tuple[date, date] | None:
    periods: list[tuple[date, date]] = []
    for match in STATEMENT_PERIOD.finditer(text):
        try:
            start = datetime.strptime(match.group("start").title(), "%B %d, %Y").date()
            end = datetime.strptime(match.group("end").title(), "%B %d, %Y").date()
        except ValueError:
            continue
        if start <= end:
            periods.append((start, end))
    unique_periods = tuple(dict.fromkeys(periods))
    return unique_periods[0] if len(unique_periods) == 1 else None


def _expand_yearless_transaction_dates(text: str) -> str:
    period = _statement_period(text)
    if period is None:
        return text
    start, end = period
    expanded: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        match = YEARLESS_DATE_AT_START.match(line)
        if match is None:
            expanded.append(raw_line)
            continue
        candidates: list[date] = []
        for year in range(start.year, end.year + 1):
            try:
                candidate = date(year, int(match.group("month")), int(match.group("day")))
            except ValueError:
                continue
            if start <= candidate <= end:
                candidates.append(candidate)
        if len(candidates) == 1:
            expanded.append(f"{candidates[0]:%m/%d/%Y} {match.group('body')}")
        else:
            expanded.append(raw_line)
    return "\n".join(expanded)


def _signed_amount(match: re.Match[str]) -> str | None:
    raw = match.group("amount")
    accounting_negative = raw.startswith("(") and raw.endswith(")")
    normalized = raw.strip("()").replace("$", "").replace(",", "")
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if amount == 0:
        return None

    directions = {
        value.casefold()
        for value in (match.group("direction_before"), match.group("direction_after"))
        if value
    }
    cues: set[int] = set()
    if accounting_negative or normalized.startswith("-"):
        cues.add(-1)
    elif normalized.startswith("+"):
        cues.add(1)
    if directions & {"dr", "debit"}:
        cues.add(-1)
    if directions & {"cr", "credit"}:
        cues.add(1)
    if len(cues) != 1:
        return None
    amount = abs(amount) * cues.pop()
    return f"{amount:.2f}"


def _ambiguous_transaction_rows() -> TransactionStatementFormatError:
    return TransactionStatementFormatError(
        "ambiguous_transaction_rows",
        "Some dated statement rows do not identify exactly one debit or credit. "
        "Use a statement whose transactions show a sign, parentheses, a debit/credit "
        "marker, or balances that prove each transaction direction.",
    )


def _description_without_money(body: str, money_matches: tuple[re.Match[str], ...]) -> str:
    parts: list[str] = []
    previous_end = 0
    for match in money_matches:
        parts.append(body[previous_end : match.start()])
        previous_end = match.end()
    parts.append(body[previous_end:])
    return " ".join(" ".join(parts).split())


def _money_value(match: re.Match[str]) -> Decimal | None:
    raw = match.group("amount")
    accounting_negative = raw.startswith("(") and raw.endswith(")")
    normalized = raw.strip("()").replace("$", "").replace(",", "")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None

    explicit = _signed_amount(match)
    has_direction = bool(match.group("direction_before") or match.group("direction_after"))
    has_sign = accounting_negative or normalized.startswith(("+", "-"))
    if value != 0 and (has_direction or has_sign):
        if explicit is None:
            return None
        return Decimal(explicit)
    return -abs(value) if accounting_negative else value


def _strict_transaction_document(text: str) -> CsvDocument:
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
            amount for match in money_matches if (amount := _signed_amount(match)) is not None
        )
        if len(directional) != 1:
            ambiguous_lines.append(line_number)
            continue
        description = _description_without_money(body, money_matches)
        if not description or len(description) > 512:
            ambiguous_lines.append(line_number)
            continue
        rows.append(
            CsvSourceRow(
                line_number,
                {
                    "Date": _iso_date(line_match.group("date")),
                    "Description": description,
                    "Amount": directional[0],
                },
            )
        )
        if len(rows) > MAX_TRANSACTIONS:
            raise TransactionStatementFormatError(
                "too_many_transactions",
                f"Transaction statements may contain at most {MAX_TRANSACTIONS} transactions.",
            )

    if ambiguous_lines:
        raise _ambiguous_transaction_rows()
    if not rows:
        raise TransactionStatementFormatError(
            "transactions_missing",
            "No unambiguous transactions were found in this statement PDF.",
        )
    return CsvDocument(headers=("Date", "Description", "Amount"), rows=tuple(rows), delimiter="pdf")


def _statement_preamble(text: str) -> tuple[str, ...]:
    preamble: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if DATE_AT_START.match(line):
            break
        preamble.append(line)
    return tuple(preamble)


def _statement_orientation(preamble: tuple[str, ...]) -> int | None:
    heading = " ".join(preamble)
    asset = ASSET_STATEMENT_LABEL.search(heading) is not None
    liability = LIABILITY_STATEMENT_LABEL.search(heading) is not None
    if asset == liability:
        return None
    return 1 if asset else -1


def _opening_balance(preamble: tuple[str, ...]) -> Decimal | None:
    balances: list[Decimal] = []
    for line in preamble:
        if OPENING_BALANCE_LABEL.search(line) is None:
            continue
        money_matches = tuple(MONEY_TOKEN.finditer(line))
        if not money_matches:
            continue
        balance = _money_value(money_matches[-1])
        if balance is not None:
            balances.append(balance)
    unique_balances = tuple(dict.fromkeys(balances))
    return unique_balances[0] if len(unique_balances) == 1 else None


def _has_amount_balance_header(preamble: tuple[str, ...]) -> bool:
    return any(
        re.search(r"\bAMOUNT\b.*\bBALANCE\b", line, re.IGNORECASE) is not None for line in preamble
    )


def _is_labeled_reference(body: str, match: re.Match[str]) -> bool:
    raw = match.group("amount")
    if any(character in raw for character in ".$,+-()"):
        return False
    prefix = body[: match.start()]
    return (
        re.search(r"\b(?:ID|REFERENCE|REF|NUMBER|NO|CARD)\s*:?[ ]*$", prefix, re.IGNORECASE)
        is not None
    )


def _looks_like_column_statement(text: str, preamble: tuple[str, ...]) -> bool:
    date_line_count = sum(
        DATE_AT_START.match(" ".join(line.split())) is not None for line in text.splitlines()
    )
    heading = " ".join(preamble).upper()
    return date_line_count >= 3 or any(
        label in heading
        for label in ("TRANSACTION", "BEGINNING BALANCE", "OPENING BALANCE", "ENDING BALANCE")
    )


def _column_transactions(
    text: str, *, allow_separated_amounts: bool
) -> tuple[_ColumnTransaction, ...]:
    transactions: list[_ColumnTransaction] = []
    ambiguous = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line_match = DATE_AT_START.match(" ".join(raw_line.split()))
        if line_match is None:
            continue
        body = line_match.group("body")
        money_matches = tuple(MONEY_TOKEN.finditer(body))
        if not money_matches:
            continue
        balance_match = money_matches[-1]
        amount_match = money_matches[-2] if len(money_matches) >= 2 else None
        if (
            amount_match is not None
            and allow_separated_amounts
            and _is_labeled_reference(body, amount_match)
        ):
            amount_match = None
        if amount_match is None and not allow_separated_amounts:
            ambiguous = True
            continue

        amount_value = _money_value(amount_match) if amount_match is not None else None
        balance = _money_value(balance_match)
        description = _description_without_money(body, money_matches)
        if (
            (amount_match is not None and amount_value is None)
            or amount_value == 0
            or balance is None
            or not description
            or len(description) > 512
        ):
            ambiguous = True
            continue
        explicit = _signed_amount(amount_match) if amount_match is not None else None
        transactions.append(
            _ColumnTransaction(
                line_number=line_number,
                date=_iso_date(line_match.group("date")),
                description=description,
                amount_magnitude=abs(amount_value) if amount_value is not None else None,
                explicit_amount=Decimal(explicit) if explicit is not None else None,
                balance=balance,
            )
        )
        if len(transactions) > MAX_TRANSACTIONS:
            raise TransactionStatementFormatError(
                "too_many_transactions",
                f"Transaction statements may contain at most {MAX_TRANSACTIONS} transactions.",
            )

    if ambiguous:
        raise _ambiguous_transaction_rows()
    if not transactions:
        raise TransactionStatementFormatError(
            "transactions_missing",
            "No unambiguous transactions were found in this statement PDF.",
        )
    return tuple(transactions)


def _observed_orientation(
    transactions: tuple[_ColumnTransaction, ...], opening_balance: Decimal | None
) -> int | None:
    observed: set[int] = set()
    previous_balance = opening_balance
    for transaction in transactions:
        if (
            previous_balance is not None
            and transaction.amount_magnitude is not None
            and transaction.explicit_amount is not None
        ):
            delta = transaction.balance - previous_balance
            if abs(delta) != transaction.amount_magnitude:
                raise _ambiguous_transaction_rows()
            observed.add(1 if delta == transaction.explicit_amount else -1)
        previous_balance = transaction.balance
    if len(observed) > 1:
        raise _ambiguous_transaction_rows()
    return next(iter(observed), None)


def _column_transaction_document(text: str, preamble: tuple[str, ...]) -> CsvDocument:
    transactions = _column_transactions(
        text, allow_separated_amounts=_has_amount_balance_header(preamble)
    )
    opening_balance = _opening_balance(preamble)
    heading_orientation = _statement_orientation(preamble)
    observed_orientation = _observed_orientation(transactions, opening_balance)
    if (
        heading_orientation is not None
        and observed_orientation is not None
        and heading_orientation != observed_orientation
    ):
        raise _ambiguous_transaction_rows()
    orientation = heading_orientation or observed_orientation

    rows: list[CsvSourceRow] = []
    previous_balance = opening_balance
    for transaction in transactions:
        if previous_balance is None:
            if transaction.explicit_amount is None:
                raise _ambiguous_transaction_rows()
            amount = transaction.explicit_amount
        else:
            delta = transaction.balance - previous_balance
            if delta == 0:
                raise _ambiguous_transaction_rows()
            if transaction.amount_magnitude is None:
                if orientation is None:
                    raise _ambiguous_transaction_rows()
                amount = delta * orientation
            else:
                if abs(delta) != transaction.amount_magnitude:
                    raise _ambiguous_transaction_rows()
                if orientation is None:
                    if transaction.explicit_amount is None:
                        raise _ambiguous_transaction_rows()
                    amount = transaction.explicit_amount
                else:
                    amount = delta * orientation
                    if (
                        transaction.explicit_amount is not None
                        and transaction.explicit_amount != amount
                    ):
                        raise _ambiguous_transaction_rows()
        rows.append(
            CsvSourceRow(
                transaction.line_number,
                {
                    "Date": transaction.date,
                    "Description": transaction.description,
                    "Amount": f"{amount:.2f}",
                },
            )
        )
        previous_balance = transaction.balance

    return CsvDocument(headers=("Date", "Description", "Amount"), rows=tuple(rows), delimiter="pdf")


def parse_transaction_statement_text(text: str) -> CsvDocument:
    """Extract transactions without guessing whether money is a debit or credit.

    Explicit signs and debit/credit markers take precedence. A column-style
    fallback is allowed only when account type and exact running-balance changes
    prove the direction of every otherwise unsigned transaction.
    """
    text = _expand_yearless_transaction_dates(text)
    try:
        return _strict_transaction_document(text)
    except TransactionStatementFormatError as exc:
        preamble = _statement_preamble(text)
        if exc.code != "ambiguous_transaction_rows" or not _looks_like_column_statement(
            text, preamble
        ):
            raise
    return _column_transaction_document(text, preamble)
