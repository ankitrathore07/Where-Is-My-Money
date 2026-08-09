from collections.abc import Mapping
from typing import cast

from app.imports.types import AmountMode, AmountSign, ColumnMapping, DateFormat

MAPPING_KEYS = {
    "date_column",
    "description_column",
    "amount_mode",
    "amount_column",
    "debit_column",
    "credit_column",
    "date_format",
    "amount_sign",
}


class MappingValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Invalid CSV column mapping")
        self.field_errors = field_errors


def _text(form: Mapping[str, object], key: str) -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else ""


def validate_mapping(headers: tuple[str, ...], form: Mapping[str, object]) -> ColumnMapping:
    """Validate source headers and return one unambiguous column mapping."""
    errors: dict[str, str] = {}
    date_column = _text(form, "date_column")
    description_column = _text(form, "description_column")
    amount_mode_value = _text(form, "amount_mode")
    date_format_value = _text(form, "date_format")
    amount_sign_value = _text(form, "amount_sign") or "as_is"

    if amount_mode_value not in {"single", "split"}:
        errors["amount_mode"] = "Choose a single amount or debit and credit columns."
    if date_format_value not in {"iso", "mdy", "dmy"}:
        errors["date_format"] = "Choose a supported date format."
    if amount_sign_value not in {"as_is", "invert"}:
        errors["amount_sign"] = "Choose how signed amounts should be interpreted."

    selected: list[tuple[str, str]] = [
        ("date_column", date_column),
        ("description_column", description_column),
    ]
    amount_column: str | None = None
    debit_column: str | None = None
    credit_column: str | None = None

    if amount_mode_value == "single":
        amount_column = _text(form, "amount_column")
        selected.append(("amount_column", amount_column))
    elif amount_mode_value == "split":
        debit_column = _text(form, "debit_column")
        credit_column = _text(form, "credit_column")
        selected.extend([("debit_column", debit_column), ("credit_column", credit_column)])
        amount_sign_value = "as_is"
        if debit_column and credit_column and debit_column == credit_column:
            errors["credit_column"] = "Debit and credit must use different columns."

    for field, value in selected:
        if not value or value not in headers:
            errors.setdefault(field, "Choose a column from this CSV.")

    used: dict[str, str] = {}
    for field, value in selected:
        if not value or value not in headers:
            continue
        if value in used and field not in errors:
            errors[field] = "Each field must use a different CSV column."
        else:
            used[value] = field

    if errors:
        raise MappingValidationError(errors)

    return ColumnMapping(
        date_column=date_column,
        description_column=description_column,
        amount_mode=cast(AmountMode, amount_mode_value),
        amount_column=amount_column,
        debit_column=debit_column,
        credit_column=credit_column,
        date_format=cast(DateFormat, date_format_value),
        amount_sign=cast(AmountSign, amount_sign_value),
    )


def mapping_from_json(headers: tuple[str, ...], value: Mapping[str, object]) -> ColumnMapping:
    """Restore persisted mapping data through the normal validator."""
    if set(value) != MAPPING_KEYS:
        raise MappingValidationError({"mapping": "Saved mapping has unexpected fields."})
    return validate_mapping(headers, value)
