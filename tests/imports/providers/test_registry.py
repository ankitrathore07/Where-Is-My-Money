from app.imports.providers.registry import resolve_provider_profile
from app.imports.types import ColumnMapping


def test_chase_bank_profile_maps_export_headers() -> None:
    result = resolve_provider_profile(
        "chase",
        "checking",
        ".csv",
        (
            "Details",
            "Posting Date",
            "Description",
            "Amount",
            "Type",
            "Balance",
            "Check or Slip #",
        ),
    )

    assert result.profile_key == "chase_bank_csv"
    assert result.recognized is True
    assert result.mapping == ColumnMapping(
        "Posting Date", "Description", "single", "Amount", None, None, "mdy", "as_is"
    )


def test_chase_credit_card_profile_maps_export_headers() -> None:
    result = resolve_provider_profile(
        "chase",
        "credit_card",
        ".csv",
        (
            "Transaction Date",
            "Post Date",
            "Description",
            "Category",
            "Type",
            "Amount",
            "Memo",
        ),
    )

    assert result.profile_key == "chase_credit_card_csv"
    assert result.recognized is True
    assert result.mapping == ColumnMapping(
        "Transaction Date", "Description", "single", "Amount", None, None, "mdy", "as_is"
    )


def test_unimplemented_institution_uses_generic_mapping() -> None:
    result = resolve_provider_profile(
        "citi", "checking", ".csv", ("Date", "Memo", "Amount")
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_selected_chase_with_unrecognized_headers_does_not_guess_another_profile() -> None:
    result = resolve_provider_profile(
        "chase", "checking", ".csv", ("Transaction Date", "Memo", "Value")
    )

    assert result.profile_key == "generic_csv"
    assert result.mapping is None
    assert result.recognized is False


def test_header_order_and_bom_whitespace_do_not_break_profile_recognition() -> None:
    result = resolve_provider_profile(
        "chase",
        "savings",
        ".csv",
        (
            "\ufeffDescription ",
            " Amount",
            "Posting Date",
            "Details",
            "Type",
            "Balance",
            "Check or Slip #",
        ),
    )

    assert result.profile_key == "chase_bank_csv"
    assert result.mapping is not None

