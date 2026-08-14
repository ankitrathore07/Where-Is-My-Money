from app.imports.providers.registry import parse_provider_pdf, resolve_provider_profile
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


def test_chase_compact_bank_profile_maps_attached_export_headers() -> None:
    result = resolve_provider_profile(
        "chase",
        "checking",
        ".csv",
        ("Date", "Description", "Amount"),
    )

    assert result.profile_key == "chase_bank_compact_csv"
    assert result.recognized is True
    assert result.mapping == ColumnMapping(
        "Date", "Description", "single", "Amount", None, None, "mdy", "as_is"
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
    result = resolve_provider_profile("citi", "checking", ".csv", ("Date", "Memo", "Amount"))

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


CHASE_PDF_TEXT = (
    "JPMorgan Chase Bank, N.A.\n"
    "Chase Checking Account Statement\n"
    "January 1, 2026 through January 31, 2026\n"
    "01/15 Remitly United S PAYMENTS 440753768551227 -$250.00\n"
)

CHASE_TOTAL_CHECKING_PDF_TEXT = (
    "JPMorgan Chase Bank, N.A.\n"
    "Chase Total Checking\n"
    "January 1, 2026 through January 31, 2026\n"
    "01/15 Remitly United S PAYMENTS 440753768551227 -$250.00\n"
)


def test_selected_chase_account_and_signed_pdf_header_use_chase_parser() -> None:
    result = parse_provider_pdf("chase", "checking", CHASE_PDF_TEXT)

    assert result is not None
    assert result.profile_key == "chase_bank_pdf"
    assert result.document.headers == ("Date", "Description", "Amount")
    assert result.document.rows[0].values == {
        "Date": "2026-01-15",
        "Description": "Remitly United S PAYMENTS 440753768551227",
        "Amount": "-250.00",
    }


def test_real_chase_total_checking_heading_uses_chase_parser() -> None:
    result = parse_provider_pdf("chase", "checking", CHASE_TOTAL_CHECKING_PDF_TEXT)

    assert result is not None
    assert result.profile_key == "chase_bank_pdf"


def test_pdf_provider_resolution_requires_account_and_statement_signatures() -> None:
    assert parse_provider_pdf("citi", "checking", CHASE_PDF_TEXT) is None
    assert parse_provider_pdf("chase", "credit_card", CHASE_PDF_TEXT) is None
    assert (
        parse_provider_pdf(
            "chase",
            "checking",
            "Checking Account Statement\n01/15 Example Market -$10.00",
        )
        is None
    )
