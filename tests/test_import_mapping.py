import pytest

from app.imports.mapping import (
    MappingValidationError,
    mapping_from_json,
    validate_mapping,
)

HEADERS = ("Date", "Description", "Amount", "Debit", "Credit")


def single_mapping(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "single",
        "amount_column": "Amount",
        "date_format": "mdy",
        "amount_sign": "as_is",
    }
    values.update(overrides)
    return values


def test_single_amount_mapping_is_typed() -> None:
    mapping = validate_mapping(HEADERS, single_mapping(amount_sign="invert"))

    assert mapping.amount_mode == "single"
    assert mapping.amount_column == "Amount"
    assert mapping.debit_column is None
    assert mapping.amount_sign == "invert"


def test_split_mapping_requires_distinct_debit_and_credit() -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(
            HEADERS,
            {
                "date_column": "Date",
                "description_column": "Description",
                "amount_mode": "split",
                "debit_column": "Debit",
                "credit_column": "Debit",
                "date_format": "iso",
            },
        )

    assert error.value.field_errors == {
        "credit_column": "Debit and credit must use different columns."
    }


@pytest.mark.parametrize("date_format", ["auto", "yyyy/mm/dd", ""])
def test_unknown_date_format_is_rejected(date_format: str) -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(HEADERS, single_mapping(date_format=date_format))

    assert error.value.field_errors["date_format"] == "Choose a supported date format."


def test_mapping_rejects_a_header_used_twice() -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(HEADERS, single_mapping(description_column="Date"))

    assert error.value.field_errors["description_column"] == (
        "Each field must use a different CSV column."
    )


def test_mapping_rejects_a_column_missing_from_the_source() -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(HEADERS, single_mapping(amount_column="Missing"))

    assert error.value.field_errors["amount_column"] == "Choose a column from this CSV."


def test_persisted_mapping_uses_the_same_validation() -> None:
    valid = validate_mapping(HEADERS, single_mapping())
    restored = mapping_from_json(HEADERS, valid.to_json())
    assert restored == valid

    malformed = valid.to_json() | {"unexpected": "value"}
    with pytest.raises(MappingValidationError) as error:
        mapping_from_json(HEADERS, malformed)
    assert error.value.field_errors["mapping"] == "Saved mapping has unexpected fields."
