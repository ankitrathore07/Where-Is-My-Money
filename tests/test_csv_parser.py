import pytest

from app.imports.parser import CsvValidationError, parse_csv_bytes


def test_parses_utf8_bom_and_semicolon_rows() -> None:
    document = parse_csv_bytes(
        "\ufeffDate;Description;Amount\n08/01/2026;Example Market;-12.34\n".encode()
    )

    assert document.headers == ("Date", "Description", "Amount")
    assert document.delimiter == ";"
    assert document.rows[0].row_number == 2
    assert document.rows[0].values["Amount"] == "-12.34"


def test_accepts_chase_rows_with_optional_surplus_trailing_empty_field() -> None:
    document = parse_csv_bytes(
        b"Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
        b"DEBIT,08/20/2026,Example payment,-10.00,ACH_DEBIT,1000.00,\n"
        b"CREDIT,08/21/2026,Example credit,20.00,ACH_CREDIT,1020.00,,\n"
        b"DSLIP,08/22/2026,Remote deposit,50.00,CHECK_DEPOSIT,1070.00,1,\n"
    )

    assert [row.values["Check or Slip #"] for row in document.rows] == ["", "", "1"]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"\xff\xfeDate", "invalid_encoding"),
        (b"Date,Description\n2026-01-01,Hello\x00World", "nul_byte"),
        (b"Date,Date\n2026-01-01,2026-01-02", "duplicate_header"),
        (b"Date,,Amount\n2026-01-01,X,1", "blank_header"),
        (b"Date|Description|Amount\n2026-01-01|X|1", "unsupported_delimiter"),
        (b"Date,Description\n2026-01-01,X,extra", "wide_row"),
        (
            b"Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            b"DSLIP,06/22/2026,Remote deposit,276.65,CHECK_DEPOSIT,4240.82,1,unexpected",
            "wide_row",
        ),
        (b"Date,Description,Amount\n2026-01-01,X", "short_row"),
    ],
)
def test_rejects_invalid_documents(data: bytes, code: str) -> None:
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(data)

    assert error.value.code == code


def test_ignores_blank_lines_but_limits_data_rows() -> None:
    body = "Date,Description,Amount\n\n" + "\n".join(
        f"2026-08-01,Example {number},-1.00" for number in range(1001)
    )

    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(body.encode())

    assert error.value.code == "too_many_rows"


def test_rejects_more_than_fifty_columns() -> None:
    headers = ",".join(f"Column {number}" for number in range(51))
    values = ",".join("x" for _ in range(51))

    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(f"{headers}\n{values}\n".encode())

    assert error.value.code == "too_many_columns"


def test_rejects_a_field_over_two_thousand_characters() -> None:
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(f"Date,Description\n2026-08-01,{'x' * 2001}\n".encode())

    assert error.value.code == "field_too_long"


def test_preserves_source_line_numbers_when_blank_rows_are_ignored() -> None:
    document = parse_csv_bytes(b"Date,Description\n\n2026-08-01,Example\n")

    assert document.rows[0].row_number == 3
