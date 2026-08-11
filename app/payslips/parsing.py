import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_TOKEN = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"
MONEY_TOKEN = r"\(?\s*\$?\s*[\d,]+(?:\.\d{1,2})?\s*\)?"
MAX_MONEY_CENTS = 2_147_483_647
MAX_MONEY_DISPLAY = "$21,474,836.47"


@dataclass(frozen=True)
class PayslipCandidate:
    employer: str | None = None
    pay_period_start: date | None = None
    pay_period_end: date | None = None
    pay_date: date | None = None
    gross_pay_cents: int | None = None
    net_pay_cents: int | None = None
    taxes_cents: int | None = None
    deductions_cents: int | None = None

    def to_json(self) -> dict[str, str | int | None]:
        """Serialize candidates for the existing JSON review column."""
        return {
            "employer": self.employer,
            "pay_period_start": (
                self.pay_period_start.isoformat() if self.pay_period_start else None
            ),
            "pay_period_end": self.pay_period_end.isoformat() if self.pay_period_end else None,
            "pay_date": self.pay_date.isoformat() if self.pay_date else None,
            "gross_pay_cents": self.gross_pay_cents,
            "net_pay_cents": self.net_pay_cents,
            "taxes_cents": self.taxes_cents,
            "deductions_cents": self.deductions_cents,
        }


@dataclass(frozen=True)
class ReviewValues:
    employer: str | None
    pay_period_start: date | None
    pay_period_end: date | None
    pay_date: date
    gross_pay_cents: int
    net_pay_cents: int
    taxes_cents: int
    deductions_cents: int


class ReviewValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the highlighted payslip fields before confirming.")
        self.field_errors = field_errors


def _normalized_lines(text: str) -> tuple[str, ...]:
    return tuple(line for raw in text.splitlines() if (line := " ".join(raw.split())))


def _first_match(lines: tuple[str, ...], pattern: str) -> str | None:
    compiled = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        if match := compiled.fullmatch(line):
            return match.group(1).strip()
    return None


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    for date_format in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def _parse_candidate_money(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().replace("$", "").replace(",", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if amount.as_tuple().exponent < -2:
        return None
    cents = int(amount * 100)
    return cents if cents <= MAX_MONEY_CENTS else None


def extract_candidates(text: str) -> PayslipCandidate:
    """Extract only explicitly labeled, deterministic payslip candidates."""
    lines = _normalized_lines(text)
    employer = _first_match(lines, r"(?:Employer|Company)\s*:?\s+(.+)")
    if employer is not None and len(employer) > 255:
        employer = None
    pay_period_match = None
    pay_period_pattern = re.compile(
        rf"Pay\s+period\s*:?\s*({DATE_TOKEN})\s*(?:-|to|through)\s*({DATE_TOKEN})",
        re.IGNORECASE,
    )
    for line in lines:
        if match := pay_period_pattern.fullmatch(line):
            pay_period_match = match
            break
    pay_date = _first_match(lines, rf"(?:Pay|Payment)\s+date\s*:?\s*({DATE_TOKEN})")
    gross = _first_match(lines, rf"(?:Gross\s+pay|Gross\s+earnings)\s*:?\s*({MONEY_TOKEN})")
    net = _first_match(lines, rf"(?:Net\s+pay|Take\s+home\s+pay)\s*:?\s*({MONEY_TOKEN})")
    taxes = _first_match(lines, rf"(?:Taxes|Total\s+taxes)\s*:?\s*({MONEY_TOKEN})")
    deductions = _first_match(lines, rf"(?:Deductions|Total\s+deductions)\s*:?\s*({MONEY_TOKEN})")
    return PayslipCandidate(
        employer=employer,
        pay_period_start=_parse_date(pay_period_match.group(1) if pay_period_match else None),
        pay_period_end=_parse_date(pay_period_match.group(2) if pay_period_match else None),
        pay_date=_parse_date(pay_date),
        gross_pay_cents=_parse_candidate_money(gross),
        net_pay_cents=_parse_candidate_money(net),
        taxes_cents=_parse_candidate_money(taxes),
        deductions_cents=_parse_candidate_money(deductions),
    )


def _review_date(
    form: Mapping[str, str],
    field: str,
    errors: dict[str, str],
    *,
    required: bool = False,
) -> date | None:
    raw_value = str(form.get(field, "")).strip()
    if not raw_value and not required:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        label = "pay date" if field == "pay_date" else "date"
        errors[field] = f"Enter a valid {label} in YYYY-MM-DD format."
        return None


def _review_money(form: Mapping[str, str], field: str, errors: dict[str, str]) -> int | None:
    raw_value = str(form.get(field, "")).strip()
    if not re.fullmatch(r"\$?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", raw_value):
        errors[field] = "Enter a non-negative amount with at most two decimal places."
        return None
    try:
        cents = int(Decimal(raw_value.replace("$", "").replace(",", "")) * 100)
    except InvalidOperation:
        errors[field] = "Enter a non-negative amount with at most two decimal places."
        return None
    if cents > MAX_MONEY_CENTS:
        errors[field] = f"Enter an amount no greater than {MAX_MONEY_DISPLAY}."
        return None
    return cents


def validate_review(form: Mapping[str, str]) -> ReviewValues:
    """Normalize editable review values independently from extracted candidates."""
    errors: dict[str, str] = {}
    employer_value = " ".join(str(form.get("employer", "")).split())
    employer = employer_value or None
    if employer is not None and len(employer) > 255:
        errors["employer"] = "Use 255 characters or fewer."

    period_start = _review_date(form, "pay_period_start", errors)
    period_end = _review_date(form, "pay_period_end", errors)
    pay_date = _review_date(form, "pay_date", errors, required=True)
    if period_start is not None and period_end is not None and period_end < period_start:
        errors["pay_period_end"] = "Pay-period end cannot be before its start."

    gross = _review_money(form, "gross_pay", errors)
    net = _review_money(form, "net_pay", errors)
    taxes = _review_money(form, "taxes", errors)
    deductions = _review_money(form, "deductions", errors)
    if errors:
        raise ReviewValidationError(errors)

    assert pay_date is not None
    assert gross is not None
    assert net is not None
    assert taxes is not None
    assert deductions is not None
    return ReviewValues(
        employer=employer,
        pay_period_start=period_start,
        pay_period_end=period_end,
        pay_date=pay_date,
        gross_pay_cents=gross,
        net_pay_cents=net,
        taxes_cents=taxes,
        deductions_cents=deductions,
    )
