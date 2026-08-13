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


def test_pdf_text_parser_infers_unsigned_checking_rows_from_running_balances() -> None:
    document = parse_transaction_statement_text(
        "Fictional Northstar Checking Statement\n"
        "Opening Balance $1,000.00\n"
        "08/01/2026 Rent 100.00 900.00\n"
        "08/02/2026 Payroll 250.00 1,150.00\n"
        "08/03/2026 Coffee 10.00 1,140.00"
    )

    assert [row.values for row in document.rows] == [
        {"Date": "2026-08-01", "Description": "Rent", "Amount": "-100.00"},
        {"Date": "2026-08-02", "Description": "Payroll", "Amount": "250.00"},
        {"Date": "2026-08-03", "Description": "Coffee", "Amount": "-10.00"},
    ]


def test_pdf_text_parser_inverts_credit_card_balance_changes() -> None:
    document = parse_transaction_statement_text(
        "Fictional Northstar Credit Card Statement\n"
        "Beginning Balance $100.00\n"
        "08/01/2026 Groceries 25.00 125.00\n"
        "08/02/2026 Payment 50.00 75.00\n"
        "08/03/2026 Coffee 5.00 80.00"
    )

    assert [row.values for row in document.rows] == [
        {"Date": "2026-08-01", "Description": "Groceries", "Amount": "-25.00"},
        {"Date": "2026-08-02", "Description": "Payment", "Amount": "50.00"},
        {"Date": "2026-08-03", "Description": "Coffee", "Amount": "-5.00"},
    ]


def test_pdf_text_parser_preserves_direction_markers_in_column_fallback() -> None:
    document = parse_transaction_statement_text(
        "Checking Statement Transactions\n"
        "Opening Balance $1,000.00\n"
        "08/01/2026 Rent DEBIT 100.00 900.00\n"
        "08/02/2026 Payroll 250.00 1,150.00\n"
        "08/03/2026 Coffee 10.00 1,140.00"
    )

    assert [row.values["Amount"] for row in document.rows] == ["-100.00", "250.00", "-10.00"]


def test_pdf_text_parser_derives_orientation_from_consistent_explicit_rows() -> None:
    document = parse_transaction_statement_text(
        "Transactions\n"
        "08/01/2026 Rent DEBIT 100.00 900.00\n"
        "08/02/2026 Payroll 250.00 1,150.00\n"
        "08/03/2026 Coffee DEBIT 10.00 1,140.00"
    )

    assert [row.values["Amount"] for row in document.rows] == ["-100.00", "250.00", "-10.00"]


@pytest.mark.parametrize(
    "text",
    [
        (
            "Transactions\n"
            "Opening Balance $1,000.00\n"
            "08/01/2026 Rent 100.00 900.00\n"
            "08/02/2026 Payroll 250.00 1,150.00\n"
            "08/03/2026 Coffee 10.00 1,140.00"
        ),
        (
            "Checking Statement Transactions\n"
            "08/01/2026 Rent 100.00 900.00\n"
            "08/02/2026 Payroll 250.00 1,150.00\n"
            "08/03/2026 Coffee 10.00 1,140.00"
        ),
        (
            "Checking Statement Transactions\n"
            "Opening Balance $1,000.00\n"
            "08/01/2026 Rent 100.00 900.00\n"
            "08/02/2026 Payroll 250.00 980.00\n"
            "08/03/2026 Coffee 10.00 970.00"
        ),
        "08/01/2026 First 100.00\n08/02/2026 Second 90.00\n08/03/2026 Third 80.00",
    ],
    ids=[
        "unknown-account-orientation",
        "missing-opening-balance",
        "amount-balance-mismatch",
        "one-money-column",
    ],
)
def test_pdf_text_parser_rejects_unsafe_column_inference(text: str) -> None:
    with pytest.raises(TransactionStatementFormatError) as error:
        parse_transaction_statement_text(text)

    assert error.value.code == "ambiguous_transaction_rows"


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
