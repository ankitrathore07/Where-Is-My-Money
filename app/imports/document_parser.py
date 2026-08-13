import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.imports.types import CsvDocument, CsvSourceRow

DATE_AT_START = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\s+(?P<body>.+)$"
)
MONEY_TOKEN = re.compile(
    r"(?<![\w.])(?:(?P<direction_before>CR|DR|CREDIT|DEBIT)\s+)?"
    r"(?P<amount>\(?[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})\)?)"
    r"(?:\s*(?P<direction_after>CR|DR|CREDIT|DEBIT))?(?![\w.])",
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


def parse_transaction_statement_text(text: str) -> CsvDocument:
    """Extract transaction rows from local PDF/OCR text.

    Strategy:
    1) Try the original strict parser that requires signed/parenthesized amounts
       or explicit debit/credit markers (preserves safety and avoids false-positives).
    2) If that finds nothing or reports ambiguous rows, fall back to a more
       permissive column-style parser that uses the rightmost numeric tokens as
       balance and amount and infers sign by comparing successive balances.
    """
    # First attempt: original strict behavior (unchanged)
    try:
        # Reuse the original strict implementation by copying its semantics here
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
        if rows:
            return CsvDocument(
                headers=("Date", "Description", "Amount"),
                rows=tuple(rows),
                delimiter="pdf",
            )
    except TransactionStatementFormatError as exc:
        # always propagate the transaction-limit error
        if getattr(exc, "code", None) == "too_many_transactions":
            raise
        # decide whether to attempt permissive fallback based on heuristics
        upper = text.upper()
        date_lines = (
            1
            for line in text.splitlines()
            if DATE_AT_START.match(" ".join(line.split()))
        )
        date_line_count = sum(date_lines)
        looks_like_statement = (
            date_line_count >= 3
            or "TRANSACTION" in upper
            or "BEGINNING BALANCE" in upper
            or "ENDING BALANCE" in upper
        )
        if not looks_like_statement:
            # preserve original strict behavior for short/ambiguous inputs
            raise

    # Fallback parser: relaxed column-style inference
    from decimal import Decimal, InvalidOperation

    fallback_rows: list[CsvSourceRow] = []
    previous_balance: Decimal | None = None
    line_iter = list(enumerate(text.splitlines(), start=1))
    for line_number, raw_line in line_iter:
        line_text = " ".join(raw_line.split())
        line_match = DATE_AT_START.match(line_text)
        if line_match is None:
            continue
        body = line_match.group("body")
        money_matches = tuple(MONEY_TOKEN.finditer(body))
        if not money_matches:
            continue
        # collect numeric tokens (as strings)
        monies = [m.group("amount") for m in money_matches]
        # description: everything with money tokens removed
        previous_end = 0
        description_parts: list[str] = []
        for m in money_matches:
            description_parts.append(body[previous_end:m.start()])
            previous_end = m.end()
        description_parts.append(body[previous_end:])
        description = " ".join(" ".join(description_parts).split())
        if not description or len(description) > 512:
            continue
        # attempt to parse balance (rightmost token) and amount (second-rightmost if present)
        balance_str = monies[-1]
        amount_str = monies[-2] if len(monies) >= 2 else None
        try:
            balance = Decimal(
                balance_str.replace("$", "").replace(",", "").strip("()")
            )
        # parentheses indicate negative balance token (rare for balances);
        # ignore sign for the parsed balance value
        except InvalidOperation:
            balance = None
        parsed_amount: str | None = None
        if amount_str is not None:
            # if amount has explicit sign/parentheses, use it
            raw_amt = amount_str.strip()
            if raw_amt.startswith("(") and raw_amt.endswith(")"):
                sign = -1
                amt_val = Decimal(raw_amt.strip("()").replace("$", "").replace(",", ""))
                parsed_amount = f"{(amt_val * sign):.2f}"
            elif raw_amt.startswith("-") or raw_amt.startswith("+"):
                try:
                    parsed_amount = f"{Decimal(raw_amt.replace("$", "").replace(",", "")):.2f}"
                except InvalidOperation:
                    parsed_amount = None
            else:
                # unsigned amount token; try to infer sign from previous balance if available
                if balance is not None and previous_balance is not None:
                    try:
                        amt_val = Decimal(amount_str.replace("$", "").replace(",", ""))
                        inferred = balance - previous_balance
                        # prefer inference where difference magnitude roughly equals token
                        if abs(inferred) == amt_val:
                            parsed_amount = f"{inferred:.2f}"
                        else:
                            # fall back to signed as positive
                            parsed_amount = f"{amt_val:.2f}"
                    except InvalidOperation:
                        parsed_amount = None
                else:
                    # no previous balance to infer sign: accept as positive
                    try:
                        amt_val = Decimal(amount_str.replace("$", "").replace(",", ""))
                        parsed_amount = f"{amt_val:.2f}"
                    except InvalidOperation:
                        parsed_amount = None
        else:
            # no explicit amount token - try to infer from balance delta
            if balance is not None and previous_balance is not None:
                inferred = balance - previous_balance
                if inferred != 0:
                    parsed_amount = f"{inferred:.2f}"
        # update previous_balance if we successfully parsed a balance
        if balance is not None:
            previous_balance = balance
        if parsed_amount is None:
            # still ambiguous; skip this line
            continue
        fallback_rows.append(
            CsvSourceRow(
                line_number,
                {
                    "Date": _iso_date(line_match.group("date")),
                    "Description": description,
                    "Amount": parsed_amount,
                },
            )
        )
        if len(fallback_rows) > MAX_TRANSACTIONS:
            raise TransactionStatementFormatError(
                "too_many_transactions",
                f"Transaction statements may contain at most {MAX_TRANSACTIONS} transactions.",
            )

    if not fallback_rows:
        raise TransactionStatementFormatError(
            "transactions_missing",
            "No unambiguous transactions were found in this statement PDF.",
        )

    return CsvDocument(
        headers=("Date", "Description", "Amount"),
        rows=tuple(fallback_rows),
        delimiter="pdf",
    )
