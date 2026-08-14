import pytest

from app.categorization.sanitization import review_group_key, sanitize_transaction_description


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Remitly United S PAYMENTS 440753768551227 CCD ID: 2452441988", "REMITLY"),
        ("RMTLY* G6271 REMITLY.COM WA 12/15", "REMITLY"),
        ("REMITLY INC REMITTANCE TBJBKKY4YD9Q9SE WEB ID: 1452441988", "REMITLY"),
        ("Payment to Chase card ending in 5851 06/22", "PAYMENT TO CHASE CARD"),
        ("COMENITY PAY IO WEB PYMT P26166061900348 WEB ID: 1651180275", "COMENITY CARD PAYMENT"),
        ("COMN CAP APY F1 AUTO PAY P26159059528913 WEB ID: 1651180275", "COMENITY CARD PAYMENT"),
        ("Wealthfront EDI PYMNTS 63DA43A1153342 WEB ID: 4271967207", "WEALTHFRONT TRANSFER"),
        (
            "REAL TIME PAYMENT CREDIT RECD FROM ABA/CONTR BNK-121000248 "
            "FROM: WEALTHFRONT BROKERAGE LLC REF: SAMPLE",
            "WEALTHFRONT TRANSFER",
        ),
        ("ROBINHOOD DEBITS 760685479 WEB ID: 5326394001", "ROBINHOOD TRANSFER"),
        (
            "Online RealTime payment to Robinhood Securities transaction#: 4965174 "
            "REFERENCE#:7004965174RX 02/12",
            "ROBINHOOD TRANSFER",
        ),
        ("BARCLAYCARD US CREDITCARD 1173314045 WEB ID: 2510407970", "BARCLAYCARD PAYMENT"),
        ("Klarna*Interview Kic Columbus OH 03/15", "KLARNA INTERVIEW KICKSTART"),
    ],
)
def test_sanitizer_canonicalizes_confirmed_chase_patterns(raw: str, expected: str) -> None:
    assert sanitize_transaction_description(raw) == expected


def test_review_group_key_is_counterparty_and_direction_aware_without_exposing_names() -> None:
    outgoing_prachi = review_group_key("Zelle payment to Prachi Rathore 30148050922", -100)
    outgoing_prachi_again = review_group_key("Zelle payment to Prachi Rathore 30064053875", -200)
    incoming_prachi = review_group_key("Zelle payment from Prachi Rathore 30148050922", 100)
    outgoing_neil = review_group_key("Zelle payment to Neil 30029789295", -100)

    assert outgoing_prachi == outgoing_prachi_again
    assert len({outgoing_prachi, incoming_prachi, outgoing_neil}) == 3
    assert "prachi" not in outgoing_prachi.casefold()
    assert "neil" not in outgoing_neil.casefold()
