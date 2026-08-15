"""Tested Citi transaction-statement profiles."""

from app.imports.providers.types import ProviderProfile
from app.imports.types import ColumnMapping

CITI_COSTCO_CREDIT_CARD_HEADERS = frozenset(
    {
        "Status",
        "Date",
        "Description",
        "Debit",
        "Credit",
        "Member Name",
    }
)

CITI_PROVIDER_PROFILES = (
    ProviderProfile(
        key="citi_costco_credit_card_csv",
        institution_key="citi",
        account_types=frozenset({"credit_card"}),
        suffixes=frozenset({".csv"}),
        required_headers=CITI_COSTCO_CREDIT_CARD_HEADERS,
        mapping=ColumnMapping(
            date_column="Date",
            description_column="Description",
            amount_mode="split",
            amount_column=None,
            debit_column="Debit",
            credit_column="Credit",
            date_format="mdy",
            amount_sign="as_is",
        ),
    ),
)
