"""Tested Capital One transaction-statement profiles."""

from app.imports.providers.types import ProviderProfile
from app.imports.types import ColumnMapping

CAPITAL_ONE_CREDIT_CARD_HEADERS = frozenset(
    {
        "Transaction Date",
        "Posted Date",
        "Card No.",
        "Description",
        "Category",
        "Debit",
        "Credit",
    }
)

CAPITAL_ONE_PROVIDER_PROFILES = (
    ProviderProfile(
        key="capital_one_credit_card_csv",
        institution_key="capital_one",
        account_types=frozenset({"credit_card"}),
        suffixes=frozenset({".csv"}),
        required_headers=CAPITAL_ONE_CREDIT_CARD_HEADERS,
        mapping=ColumnMapping(
            date_column="Transaction Date",
            description_column="Description",
            amount_mode="split",
            amount_column=None,
            debit_column="Debit",
            credit_column="Credit",
            date_format="iso",
            amount_sign="as_is",
        ),
    ),
)
