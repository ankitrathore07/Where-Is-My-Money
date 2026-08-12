from pathlib import Path

import pytest

import app.imports.document_parser as document_parser
from app.imports.document_parser import (
    TransactionStatementFormatError,
    parse_transaction_statement_text,
)


def test_pdf_text_parser_extracts_only_explicitly_directional_transactions() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "statements" / "synthetic_transaction_pdf_text.txt"
    )
    document = parse_transaction_statement_text(fixture.read_text(encoding="utf-8"))

    assert document.headers == ("Date", "Description", "Amount")
    assert [row.values for row in document.rows] == [
        {"Date": "2026-08-01", "Description": "Example Market", "Amount": "-12.34"},
        {"Date": "2026-08-02", "Description": "Payroll", "Amount": "2500.00"},
        {"Date": "2026-08-03", "Description": "Coffee", "Amount": "-4.50"},
    ]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("2026-08-01 Minus -$1.25", "-1.25"),
        ("08/02/2026 Plus +$2.50", "2.50"),
        ("08/03/26 Prefix debit DEBIT $3.75", "-3.75"),
        ("08/04/69 Prefix credit CREDIT $4.00", "4.00"),
        ("08/05/70 Suffix debit $5.25 DR", "-5.25"),
        ("08/06/2026 Suffix credit $6.50 CR", "6.50"),
        ("08/07/2026 Parentheses ($7.75)", "-7.75"),
    ],
)
def test_pdf_text_parser_supports_every_documented_direction_form(row: str, expected: str) -> None:
    document = parse_transaction_statement_text(row)

    assert document.rows[0].values["Amount"] == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("2026-02-30 Invalid date -$1.00", "invalid_transaction_date"),
        ("08/01/2026 Conflicting +$1.00 DEBIT", "ambiguous_transaction_rows"),
        ("08/01/2026 Two amounts -$1.00 +$2.00", "ambiguous_transaction_rows"),
        ("08/01/2026 Zero +$0.00", "ambiguous_transaction_rows"),
        (f"08/01/2026 {'x' * 513} -$1.00", "ambiguous_transaction_rows"),
    ],
)
def test_pdf_text_parser_rejects_malformed_or_ambiguous_rows(text: str, code: str) -> None:
    with pytest.raises(TransactionStatementFormatError) as error:
        parse_transaction_statement_text(text)

    assert error.value.code == code


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("08/01/2026 Example Market $12.34", "ambiguous_transaction_rows"),
        ("Statement period ending 08/01/2026", "transactions_missing"),
    ],
)
def test_pdf_text_parser_never_guesses_transaction_direction(text: str, code: str) -> None:
    with pytest.raises(TransactionStatementFormatError) as error:
        parse_transaction_statement_text(text)
    assert error.value.code == code


def test_pdf_text_parser_enforces_transaction_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(document_parser, "MAX_TRANSACTIONS", 2)

    with pytest.raises(TransactionStatementFormatError) as error:
        parse_transaction_statement_text(
            "08/01/2026 First -$1.00\n08/02/2026 Second -$2.00\n08/03/2026 Third -$3.00"
        )

    assert error.value.code == "too_many_transactions"


def test_pdf_text_parser_is_repeatable() -> None:
    text = "08/01/2026 Example -$12.34\n2026-08-02 Payroll $2,500.00 CR"

    assert parse_transaction_statement_text(text) == parse_transaction_statement_text(text)
