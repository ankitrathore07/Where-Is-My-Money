"""Tested Chase CSV statement profiles."""

from app.imports.providers.types import ProviderProfile
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
