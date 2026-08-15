from app.imports.providers.citi import CITI_PROVIDER_PROFILES
from app.imports.providers.registry import resolve_provider_profile
from app.imports.types import ColumnMapping

CITI_COSTCO_CREDIT_CARD_HEADERS = (
    "Status",
    "Date",
    "Description",
    "Debit",
    "Credit",
    "Member Name",
)


def test_citi_costco_credit_card_profile_maps_sample_export_headers() -> None:
    result = resolve_provider_profile(
        "citi",
        "credit_card",
        ".csv",
        CITI_COSTCO_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "citi_costco_credit_card_csv"
    assert result.recognized is True
    assert result.mapping == ColumnMapping(
        date_column="Date",
        description_column="Description",
        amount_mode="split",
        amount_column=None,
        debit_column="Debit",
        credit_column="Credit",
        date_format="mdy",
        amount_sign="as_is",
    )


def test_citi_costco_profile_is_explicitly_scoped() -> None:
    assert len(CITI_PROVIDER_PROFILES) == 1
    profile = CITI_PROVIDER_PROFILES[0]

    assert profile.key == "citi_costco_credit_card_csv"
    assert profile.institution_key == "citi"
    assert profile.account_types == frozenset({"credit_card"})
    assert profile.suffixes == frozenset({".csv"})
    assert profile.required_headers == frozenset(CITI_COSTCO_CREDIT_CARD_HEADERS)


def test_citi_headers_do_not_override_selected_institution() -> None:
    result = resolve_provider_profile(
        "capital_one",
        "credit_card",
        ".csv",
        CITI_COSTCO_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_citi_costco_profile_rejects_incompatible_account_type() -> None:
    result = resolve_provider_profile(
        "citi",
        "checking",
        ".csv",
        CITI_COSTCO_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_citi_costco_profile_requires_all_sample_headers() -> None:
    result = resolve_provider_profile(
        "citi",
        "credit_card",
        ".csv",
        tuple(header for header in CITI_COSTCO_CREDIT_CARD_HEADERS if header != "Credit"),
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False
