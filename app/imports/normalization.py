import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.imports.types import ColumnMapping, CsvSourceRow, DateFormat, NormalizedTransaction

DATE_FORMATS: dict[DateFormat, str] = {
    "iso": "%Y-%m-%d",
    "mdy": "%m/%d/%Y",
    "dmy": "%d/%m/%Y",
}
MONEY_PATTERN = re.compile(r"^[+-]?\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$")


class RowValidationError(ValueError):
    def __init__(self, row_number: int, field_errors: dict[str, str]) -> None:
        super().__init__(f"CSV row {row_number} is invalid")
        self.row_number = row_number
        self.field_errors = field_errors


def _parse_date(value: str, date_format: DateFormat) -> datetime:
    return datetime.strptime(value.strip(), DATE_FORMATS[date_format])


def _clean_description(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


def _parse_cents(value: str, *, allow_zero: bool = False) -> int:
    text = value.strip()
    accounting_negative = text.startswith("(") and text.endswith(")")
    if "(" in text or ")" in text:
        if not accounting_negative:
            raise ValueError
        text = text[1:-1].strip()
        if text.startswith(("+", "-")):
            raise ValueError
    if not MONEY_PATTERN.fullmatch(text):
        raise ValueError
    normalized = text.replace("$", "").replace(",", "")
    try:
        decimal_value = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError from exc
    if not decimal_value.is_finite() or decimal_value.as_tuple().exponent < -2:
        raise ValueError
    cents = int(decimal_value * 100)
    if accounting_negative:
        cents = -abs(cents)
    if cents == 0 and not allow_zero:
        raise ValueError
    return cents


def _normalized_transaction(
    row_number: int,
    date_value: str,
    description_value: str,
    amount_cents: int | None,
    date_format: DateFormat,
    amount_error: str | None = None,
) -> NormalizedTransaction:
    errors: dict[str, str] = {}
    try:
        transaction_date = _parse_date(date_value, date_format).date()
    except (KeyError, ValueError):
        errors["date"] = "Enter a date in the selected format."
        transaction_date = None

    description = _clean_description(description_value)
    if not description:
        errors["description"] = "Enter a description."
    elif len(description) > 512:
        errors["description"] = "Descriptions may contain at most 512 characters."

    if amount_error is not None or amount_cents is None or amount_cents == 0:
        errors["amount"] = amount_error or "Enter a valid non-zero amount."

    if errors:
        raise RowValidationError(row_number, errors)
    assert transaction_date is not None
    assert amount_cents is not None
    return NormalizedTransaction(
        row_number=row_number,
        transaction_date=transaction_date,
        description=description,
        normalized_merchant=description.upper(),
        amount_cents=amount_cents,
    )


def normalize_source_row(row: CsvSourceRow, mapping: ColumnMapping) -> NormalizedTransaction:
    """Normalize one mapped CSV source row without float arithmetic."""
    amount_cents: int | None = None
    amount_error: str | None = None
    try:
        if mapping.amount_mode == "single":
            assert mapping.amount_column is not None
            amount_cents = _parse_cents(row.values[mapping.amount_column])
            if mapping.amount_sign == "invert":
                amount_cents *= -1
        else:
            assert mapping.debit_column is not None
            assert mapping.credit_column is not None
            debit_text = row.values[mapping.debit_column].strip()
            credit_text = row.values[mapping.credit_column].strip()
            debit = _parse_cents(debit_text, allow_zero=True) if debit_text else 0
            credit = _parse_cents(credit_text, allow_zero=True) if credit_text else 0
            if debit and credit:
                amount_error = "Enter a debit or credit, not both."
            else:
                amount_cents = -abs(debit) if debit else abs(credit)
    except (KeyError, ValueError):
        amount_error = "Enter a valid non-zero amount."

    return _normalized_transaction(
        row.row_number,
        row.values.get(mapping.date_column, ""),
        row.values.get(mapping.description_column, ""),
        amount_cents,
        mapping.date_format,
        amount_error,
    )


def normalize_review_edit(
    row_number: int,
    date_value: str,
    description_value: str,
    amount_value: str,
    date_format: DateFormat,
) -> NormalizedTransaction:
    """Validate one user-edited review row using its displayed signed amount."""
    try:
        amount_cents = _parse_cents(amount_value)
        amount_error = None
    except ValueError:
        amount_cents = None
        amount_error = "Enter a valid non-zero amount."
    return _normalized_transaction(
        row_number,
        date_value,
        description_value,
        amount_cents,
        date_format,
        amount_error,
    )
