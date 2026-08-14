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


def test_sanitizer_removes_controls_and_caps_network_text() -> None:
    sanitized = sanitize_transaction_description("SHOP\x00\x1f " + "X" * 300)

    assert sanitized.startswith("SHOP ")
    assert len(sanitized) == 160
    assert all(character.isprintable() for character in sanitized)
