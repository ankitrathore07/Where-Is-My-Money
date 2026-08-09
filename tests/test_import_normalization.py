from datetime import date

import pytest

from app.imports.normalization import (
    RowValidationError,
    normalize_review_edit,
    normalize_source_row,
)
from app.imports.types import ColumnMapping, CsvSourceRow


def single_mapping(**changes: object) -> ColumnMapping:
    values: dict[str, object] = {
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "single",
        "amount_column": "Amount",
        "debit_column": None,
        "credit_column": None,
        "date_format": "mdy",
        "amount_sign": "as_is",
    }
    values.update(changes)
    return ColumnMapping(**values)  # type: ignore[arg-type]


def test_normalizes_accounting_money_and_unicode_description() -> None:
    row = CsvSourceRow(
        2,
        {
            "Date": "08/01/2026",
            "Description": "  Café   Market  ",
            "Amount": "($1,234.50)",
        },
    )

    transaction = normalize_source_row(row, single_mapping())

    assert transaction.transaction_date == date(2026, 8, 1)
    assert transaction.description == "Café Market"
    assert transaction.normalized_merchant == "CAFÉ MARKET"
    assert transaction.amount_cents == -123_450


def test_single_amount_can_invert_statement_signs() -> None:
    row = CsvSourceRow(
        2,
        {"Date": "2026-08-01", "Description": "Market", "Amount": "12.34"},
    )

    transaction = normalize_source_row(row, single_mapping(date_format="iso", amount_sign="invert"))

    assert transaction.amount_cents == -1234


def test_split_debit_is_negative_and_credit_is_positive() -> None:
    mapping = ColumnMapping("Date", "Description", "split", None, "Debit", "Credit", "iso", "as_is")
    debit = normalize_source_row(
        CsvSourceRow(
            2, {"Date": "2026-08-01", "Description": "Store", "Debit": "4.20", "Credit": ""}
        ),
        mapping,
    )
    credit = normalize_source_row(
        CsvSourceRow(
            3, {"Date": "2026-08-02", "Description": "Refund", "Debit": "", "Credit": "4.20"}
        ),
        mapping,
    )

    assert debit.amount_cents == -420
    assert credit.amount_cents == 420


@pytest.mark.parametrize("amount", ["NaN", "1.001", "0", "$", "1e9", "1,2.00"])
def test_invalid_money_has_an_amount_error(amount: str) -> None:
    with pytest.raises(RowValidationError) as error:
        normalize_review_edit(2, "08/01/2026", "Store", amount, "mdy")

    assert error.value.field_errors["amount"] == "Enter a valid non-zero amount."


def test_split_row_rejects_two_nonzero_values() -> None:
    mapping = ColumnMapping("Date", "Description", "split", None, "Debit", "Credit", "dmy", "as_is")

    with pytest.raises(RowValidationError) as error:
        normalize_source_row(
            CsvSourceRow(
                2, {"Date": "01/08/2026", "Description": "Store", "Debit": "1", "Credit": "2"}
            ),
            mapping,
        )

    assert error.value.field_errors["amount"] == "Enter a debit or credit, not both."


def test_description_and_date_errors_are_returned_together() -> None:
    with pytest.raises(RowValidationError) as error:
        normalize_review_edit(9, "02/30/2026", "   ", "1.00", "mdy")

    assert error.value.row_number == 9
    assert error.value.field_errors == {
        "date": "Enter a date in the selected format.",
        "description": "Enter a description.",
    }


def test_description_over_database_limit_is_rejected() -> None:
    with pytest.raises(RowValidationError) as error:
        normalize_review_edit(2, "2026-08-01", "x" * 513, "1.00", "iso")

    assert error.value.field_errors["description"] == (
        "Descriptions may contain at most 512 characters."
    )
