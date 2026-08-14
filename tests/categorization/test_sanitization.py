import pytest

from app.categorization.sanitization import sanitize_transaction_description


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BEST BUY AUTO PYMT 240812 123456789012345", "BEST BUY AUTO PYMT"),
        ("CITI CARD ONLINE PAYMENT 240731 998877665544", "CITI CARD ONLINE PAYMENT"),
        ("ZELLE PAYMENT TO JANE SAMPLE 123456789", "ZELLE PAYMENT TO <PAYEE>"),
        ("ZELLE PAYMENT FROM JOHN SAMPLE 987654321", "ZELLE PAYMENT FROM <PAYER>"),
        ("  Microsoft\tEDIPAYMENT  ", "MICROSOFT EDIPAYMENT"),
        ("PAYMENT ACCT 123456789 RECURRING", "PAYMENT ACCT <ID> RECURRING"),
    ],
)
def test_sanitizer_removes_identifying_values_without_losing_merchant_words(
    raw: str, expected: str
) -> None:
    assert sanitize_transaction_description(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "CAPITAL ONE MOBILE PMT CA003347893934B WEB ID: 9279744380",
            "CAPITAL ONE MOBILE PMT",
        ),
        (
            "CITI CARD ONLINE PAYMENT 422066576858704 WEB ID: CITICTP",
            "CITI CARD ONLINE PAYMENT",
        ),
        (
            "BEST BUY AUTO PYMT 722054485830145 WEB ID: CITIAUTFDR",
            "BEST BUY AUTO PYMT",
        ),
        ("NEWREZ-SHELLPOIN ACH PMT PPD ID: 6371542226", "NEWREZ-SHELLPOIN ACH PMT"),
        ("MICROSOFT EDIPAYMENT PPD ID: 9911144442", "MICROSOFT EDIPAYMENT"),
        (
            "MICROSOFT 1064681834 15105357671010 CTX ID: 8359993246",
            "MICROSOFT CTX",
        ),
        ("XOOM DEBIT OID 30178544 WEB ID: 1770510487", "XOOM DEBIT"),
        ("REMOTE ONLINE DEPOSIT # 1", "REMOTE ONLINE DEPOSIT"),
    ],
)
def test_sanitizer_handles_real_chase_descriptions(raw: str, expected: str) -> None:
    assert sanitize_transaction_description(raw) == expected


def test_sanitizer_removes_controls_and_caps_network_text() -> None:
    sanitized = sanitize_transaction_description("SHOP\x00\x1f " + "X" * 300)

    assert sanitized.startswith("SHOP ")
    assert len(sanitized) == 160
    assert all(character.isprintable() for character in sanitized)
