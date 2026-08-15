from app.imports.providers.capital_one import CAPITAL_ONE_PROVIDER_PROFILES
from app.imports.providers.registry import resolve_provider_profile
from app.imports.types import ColumnMapping

CAPITAL_ONE_CREDIT_CARD_HEADERS = (
    "Transaction Date",
    "Posted Date",
    "Card No.",
    "Description",
    "Category",
    "Debit",
    "Credit",
)


def test_capital_one_credit_card_profile_maps_sample_export_headers() -> None:
    result = resolve_provider_profile(
        "capital_one",
        "credit_card",
        ".csv",
        CAPITAL_ONE_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "capital_one_credit_card_csv"
    assert result.recognized is True
    assert result.mapping == ColumnMapping(
        date_column="Transaction Date",
        description_column="Description",
        amount_mode="split",
        amount_column=None,
        debit_column="Debit",
        credit_column="Credit",
        date_format="iso",
        amount_sign="as_is",
    )


def test_capital_one_profile_is_explicitly_scoped() -> None:
    assert len(CAPITAL_ONE_PROVIDER_PROFILES) == 1
    profile = CAPITAL_ONE_PROVIDER_PROFILES[0]

    assert profile.key == "capital_one_credit_card_csv"
    assert profile.institution_key == "capital_one"
    assert profile.account_types == frozenset({"credit_card"})
    assert profile.suffixes == frozenset({".csv"})
    assert profile.required_headers == frozenset(CAPITAL_ONE_CREDIT_CARD_HEADERS)


def test_capital_one_headers_do_not_override_selected_institution() -> None:
    result = resolve_provider_profile(
        "citi",
        "credit_card",
        ".csv",
        CAPITAL_ONE_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_capital_one_profile_rejects_incompatible_account_type() -> None:
    result = resolve_provider_profile(
        "capital_one",
        "checking",
        ".csv",
        CAPITAL_ONE_CREDIT_CARD_HEADERS,
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_capital_one_profile_requires_all_sample_headers() -> None:
    result = resolve_provider_profile(
        "capital_one",
        "credit_card",
        ".csv",
        tuple(header for header in CAPITAL_ONE_CREDIT_CARD_HEADERS if header != "Credit"),
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False
