import pytest

from app.imports.document_parser import (
    TransactionStatementFormatError,
    parse_transaction_statement_text,
)


def test_pdf_text_parser_extracts_only_explicitly_directional_transactions() -> None:
    document = parse_transaction_statement_text(
        "Statement date 2026-08-01\n"
        "08/01/2026 Example Market -$12.34 $1,250.00\n"
        "2026-08-02 Payroll $2,500.00 CR\n"
        "08/03/26 Coffee ($4.50)\n"
    )

    assert document.headers == ("Date", "Description", "Amount")
    assert [row.values for row in document.rows] == [
        {"Date": "2026-08-01", "Description": "Example Market", "Amount": "-12.34"},
        {"Date": "2026-08-02", "Description": "Payroll", "Amount": "2500.00"},
        {"Date": "2026-08-03", "Description": "Coffee", "Amount": "-4.50"},
    ]


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
