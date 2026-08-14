"""Tested Chase transaction-statement profiles."""

import re

from app.imports.document_parser import parse_transaction_statement_text
from app.imports.providers.types import ProviderPdfProfile, ProviderProfile
from app.imports.types import ColumnMapping

CHASE_BANK_HEADERS = frozenset(
    {
        "Details",
        "Posting Date",
        "Description",
        "Amount",
        "Type",
        "Balance",
        "Check or Slip #",
    }
)
CHASE_BANK_COMPACT_HEADERS = frozenset({"Date", "Description", "Amount"})
CHASE_CREDIT_CARD_HEADERS = frozenset(
    {
        "Transaction Date",
        "Post Date",
        "Description",
        "Category",
        "Type",
        "Amount",
        "Memo",
    }
)

CHASE_PROVIDER_PROFILES = (
    ProviderProfile(
        key="chase_bank_csv",
        institution_key="chase",
        account_types=frozenset({"checking", "savings"}),
        suffixes=frozenset({".csv"}),
        required_headers=CHASE_BANK_HEADERS,
        mapping=ColumnMapping(
            date_column="Posting Date",
            description_column="Description",
            amount_mode="single",
            amount_column="Amount",
            debit_column=None,
            credit_column=None,
            date_format="mdy",
            amount_sign="as_is",
        ),
    ),
    ProviderProfile(
        key="chase_bank_compact_csv",
        institution_key="chase",
        account_types=frozenset({"checking", "savings"}),
        suffixes=frozenset({".csv"}),
        required_headers=CHASE_BANK_COMPACT_HEADERS,
        mapping=ColumnMapping(
            date_column="Date",
            description_column="Description",
            amount_mode="single",
            amount_column="Amount",
            debit_column=None,
            credit_column=None,
            date_format="mdy",
            amount_sign="as_is",
        ),
    ),
    ProviderProfile(
        key="chase_credit_card_csv",
        institution_key="chase",
        account_types=frozenset({"credit_card"}),
        suffixes=frozenset({".csv"}),
        required_headers=CHASE_CREDIT_CARD_HEADERS,
        mapping=ColumnMapping(
            date_column="Transaction Date",
            description_column="Description",
            amount_mode="single",
            amount_column="Amount",
            debit_column=None,
            credit_column=None,
            date_format="mdy",
            amount_sign="as_is",
        ),
    ),
)

_TRANSACTION_DATE_LINE = re.compile(r"^\s*(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
_CHASE_BANK_NAME = re.compile(r"\b(?:JPMORGAN\s+CHASE\s+BANK|CHASE)\b", re.IGNORECASE)
_BANK_STATEMENT = re.compile(r"\b(?:CHECKING|SAVINGS)(?:\s+ACCOUNT)?\s+STATEMENT\b", re.IGNORECASE)


def _chase_pdf_preamble(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if _TRANSACTION_DATE_LINE.match(line):
            break
        lines.append(line)
    return "\n".join(lines)


def _matches_chase_bank_pdf(text: str) -> bool:
    preamble = _chase_pdf_preamble(text)
    return bool(_CHASE_BANK_NAME.search(preamble) and _BANK_STATEMENT.search(preamble))


CHASE_PDF_PROFILES = (
    ProviderPdfProfile(
        key="chase_bank_pdf",
        institution_key="chase",
        account_types=frozenset({"checking", "savings"}),
        matches=_matches_chase_bank_pdf,
        parse=parse_transaction_statement_text,
    ),
)
