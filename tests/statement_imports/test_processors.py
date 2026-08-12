from datetime import date

import pytest

from app.statement_imports.processors import process_statement_text
from app.statement_imports.types import (
    SUPPORTED_STATEMENT_CATEGORIES,
    StatementFormatError,
    compatible_account_types,
)


def test_supported_categories_map_only_to_implemented_account_types() -> None:
    assert SUPPORTED_STATEMENT_CATEGORIES == (
        "investment_401k",
        "brokerage",
        "mortgage",
        "loan",
        "other",
    )
    assert compatible_account_types("investment_401k") == frozenset({"investment_401k"})
    assert compatible_account_types("brokerage") == frozenset({"investment_brokerage"})
    assert compatible_account_types("mortgage") == frozenset({"mortgage"})
    assert compatible_account_types("loan") == frozenset({"auto_loan", "student_loan"})
    assert compatible_account_types("other") == frozenset({"other"})


@pytest.mark.parametrize(
    ("category", "label"),
    [
        ("investment_401k", "Total account balance"),
        ("investment_401k", "Total plan balance"),
        ("investment_401k", "Ending account value"),
        ("investment_401k", "Account value"),
        ("brokerage", "Total account value"),
        ("brokerage", "Ending account value"),
        ("brokerage", "Net account value"),
        ("brokerage", "Portfolio value"),
        ("mortgage", "Unpaid principal balance"),
        ("mortgage", "Current principal balance"),
        ("mortgage", "Remaining principal balance"),
        ("loan", "Outstanding principal balance"),
        ("loan", "Current principal balance"),
        ("loan", "Remaining principal balance"),
        ("other", "Total balance"),
        ("other", "Ending balance"),
        ("other", "Current balance"),
    ],
)
def test_processor_accepts_only_documented_category_balance_labels(
    category: str, label: str
) -> None:
    text = (
        "Provider: Northstar Financial\n"
        "Account ending in: 4821\n"
        "Statement date: July 31, 2026\n"
        f"{label}: $125,430.18"
    )

    candidate = process_statement_text(category, text, "embedded_text")

    assert candidate.account_name == "Account ending in 4821"
    assert candidate.institution == "Northstar Financial"
    assert candidate.account_last_four == "4821"
    assert candidate.balance_cents == 12_543_018
    assert candidate.as_of_date == date(2026, 7, 31)


def test_processor_accepts_repeated_identical_values_and_reduces_full_account_number() -> None:
    candidate = process_statement_text(
        "mortgage",
        "Servicer: Northstar Home Loans\nAccount number: 000099887742\n"
        "As of date: 07/31/2026\nStatement date: 2026-07-31\n"
        "Unpaid principal balance: $248,125.44\n"
        "Current principal balance: $248,125.44",
        "ocr",
    )
    assert candidate.account_last_four == "7742"
    assert candidate.balance_cents == 24_812_544
    assert candidate.extraction_method == "ocr"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (
            "Provider: A\nAccount number: 1234\nStatement date: 2026-07-31\n"
            "Total account value: $100.00\nPortfolio value: $200.00",
            "ambiguous_balance",
        ),
        (
            "Provider: A\nAccount number: 1234\nStatement date: 2026-07-31\n"
            "As of date: 2026-07-30\nTotal account value: $100.00",
            "ambiguous_date",
        ),
        (
            "Provider: A\nAccount ending in 1234\nAccount ending in 5678\n"
            "Statement date: 2026-07-31\nTotal account value: $100.00",
            "ambiguous_identity",
        ),
        (
            "Provider: A\nAccount number: 1234\nStatement date: 2026-07-31\n"
            "Buying power: $100.00\nAmount due: $100.00\nHoldings: $100.00",
            "missing_balance",
        ),
        (
            "Provider: A\nAccount number: 1234\nStatement date: 2026-07-31\n"
            "Total account value: ($100.00)",
            "invalid_balance",
        ),
    ],
)
def test_processor_rejects_ambiguous_or_non_total_values(text: str, code: str) -> None:
    with pytest.raises(StatementFormatError) as error:
        process_statement_text("brokerage", text, "embedded_text")
    assert error.value.code == code
