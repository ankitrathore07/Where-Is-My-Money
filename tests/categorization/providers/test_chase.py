import pytest

from app.categorization.providers.chase import find_provider_rule


@pytest.mark.parametrize(
    ("description", "amount_cents", "merchant", "category"),
    [
        ("CITI CARD ONLINE PAYMENT 240812 123456789", -10000, "Citi Card Payment", "Transfers"),
        ("CAPITAL ONE MOBILE PMT 240812 123456789", -10000, "Capital One Payment", "Transfers"),
        ("BEST BUY AUTO PYMT 240812 123456789", -2999, "Best Buy Card Payment", "Transfers"),
        ("BEST BUY AUTO PAYMENT 240812 123456789", -2999, "Best Buy Card Payment", "Transfers"),
        (
            "BEST BUY PAYMENT 631743239636068 WEB ID: CITICTP",
            -2999,
            "Best Buy Card Payment",
            "Transfers",
        ),
        ("NEWREZ-SHELLPOINT ACH PMT 240812 123456789", -476114, "Newrez Mortgage", "Housing"),
        ("NEWREZ-SHELLPOIN ACH PMT PPD ID: 6371542226", -511686, "Newrez Mortgage", "Housing"),
        ("MICROSOFT EDIPAYMENT PPD ID: 9911144442", 310111, "Microsoft Income", "Income"),
        (
            "MICROSOFT 1064681834 15105357671010 CTX ID: 8359993246",
            500000,
            "Microsoft Income",
            "Income",
        ),
        ("XOOM DEBIT OID 30178544 WEB ID: 1770510487", -1499, "Xoom", "Gifts & Donations"),
        ("REMOTE ONLINE DEPOSIT # 1", 46054, "Remote Online Deposit", "Income"),
    ],
)
def test_confirmed_chase_bank_patterns_are_deterministic(
    description: str,
    amount_cents: int,
    merchant: str,
    category: str,
) -> None:
    rule = find_provider_rule("chase_bank_csv", description, amount_cents)

    assert rule is not None
    assert rule.normalized_merchant == merchant
    assert rule.category_name == category
    assert rule.is_subscription is False


@pytest.mark.parametrize(
    ("description", "amount_cents"),
    [
        ("SHOP BEST BUY AUTO PYMT", -2999),
        ("ZELLE PAYMENT TO SAMPLE", 5000),
        ("ZELLE PAYMENT TO SAMPLE", -5000),
        ("ZELLE PAYMENT FROM SAMPLE", 5000),
        ("XOOM DEBIT 123456789", 1499),
        ("REMOTE ONLINE DEPOSIT 123456789", -46054),
    ],
)
def test_unconfirmed_partial_or_wrong_direction_patterns_do_not_match(
    description: str, amount_cents: int
) -> None:
    assert find_provider_rule("chase_bank_csv", description, amount_cents) is None


def test_chase_rules_do_not_apply_to_generic_or_credit_card_profiles() -> None:
    description = "BEST BUY AUTO PYMT 240812 123456789"

    assert find_provider_rule(None, description, -2999) is None
    assert find_provider_rule("generic_csv", description, -2999) is None
    assert find_provider_rule("chase_credit_card_csv", description, -2999) is None


def test_chase_rules_apply_to_compact_bank_profile() -> None:
    rule = find_provider_rule(
        "chase_bank_compact_csv",
        "CAPITAL ONE MOBILE PMT CA003347893934B WEB ID: 9279744380",
        -20000,
    )

    assert rule is not None
    assert rule.category_name == "Transfers"


@pytest.mark.parametrize(
    ("description", "amount_cents", "merchant", "category", "tags", "cadence"),
    [
        (
            "Remitly United S PAYMENTS 440753768551227 CCD ID: 2452441988",
            -25000,
            "Remitly",
            "Gifts & Donations",
            ("Family Support",),
            None,
        ),
        (
            "Payment to Chase card ending in 5851 06/22",
            -20000,
            "Chase Card Payment",
            "Transfers",
            (),
            None,
        ),
        (
            "Wealthfront EDI PYMNTS 63DA43A1153342 WEB ID: 4271967207",
            -100000,
            "Wealthfront Savings Transfer",
            "Transfers",
            (),
            None,
        ),
        (
            "REAL TIME PAYMENT CREDIT RECD FROM ABA/CONTR BNK-121000248 "
            "FROM: WEALTHFRONT BROKERAGE LLC REF: SAMPLE",
            100000,
            "Wealthfront Savings Transfer",
            "Transfers",
            (),
            None,
        ),
        (
            "COMENITY PAY IO WEB PYMT P26166061900348 WEB ID: 1651180275",
            -10000,
            "Comenity Card Payment",
            "Transfers",
            (),
            None,
        ),
        (
            "COMN CAP APY F1 AUTO PAY P26159059528913 WEB ID: 1651180275",
            -10000,
            "Comenity Card Payment",
            "Transfers",
            (),
            None,
        ),
        (
            "ROBINHOOD DEBITS 760685479 WEB ID: 5326394001",
            -30000,
            "Robinhood Transfer",
            "Transfers",
            (),
            None,
        ),
        (
            "Online RealTime payment to Robinhood Securities transaction#: 4965174 "
            "REFERENCE#:7004965174RX 02/12",
            -30000,
            "Robinhood Transfer",
            "Transfers",
            (),
            None,
        ),
        (
            "BARCLAYCARD US CREDITCARD 1173314045 WEB ID: 2510407970",
            -10000,
            "Barclaycard Payment",
            "Transfers",
            (),
            None,
        ),
        ("DOMESTIC WIRE FEE", -2500, "Domestic Wire Fee", "Taxes & Fees", (), None),
        ("OFFICIAL CHECKS CHARGE", -1000, "Official Check Fee", "Taxes & Fees", (), None),
        (
            "IRS TREAS 310 TAX REF PPD ID: 9111036170",
            50000,
            "IRS Tax Refund",
            "Income",
            ("Tax Refund",),
            None,
        ),
        (
            "Klarna*Interview Kic Columbus OH 03/15",
            -50000,
            "Interview Kickstart",
            "Education",
            ("Installment Plan",),
            1,
        ),
    ],
)
def test_new_confirmed_chase_rules(
    description: str,
    amount_cents: int,
    merchant: str,
    category: str,
    tags: tuple[str, ...],
    cadence: int | None,
) -> None:
    rule = find_provider_rule("chase_bank_compact_csv", description, amount_cents)

    assert rule is not None
    assert rule.normalized_merchant == merchant
    assert rule.category_name == category
    assert rule.tag_names == tags
    assert rule.billing_period_months == cadence


@pytest.mark.parametrize(
    "description",
    [
        "Remitly United S PAYMENTS 440753768551227 CCD ID: 2452441988",
        "Payment to Chase card ending in 5851 06/22",
        "COMENITY PAY IO WEB PYMT P26166061900348 WEB ID: 1651180275",
        "IRS TREAS 310 TAX REF PPD ID: 9111036170",
    ],
)
def test_new_chase_rules_reject_wrong_direction(description: str) -> None:
    expected_direction_amount = -100 if "IRS TREAS" in description else 100
    assert (
        find_provider_rule("chase_bank_compact_csv", description, expected_direction_amount) is None
    )


def test_generic_zelle_stays_unresolved() -> None:
    assert (
        find_provider_rule(
            "chase_bank_compact_csv", "Zelle payment to a local business 123456789", -5000
        )
        is None
    )
    assert (
        find_provider_rule("chase_bank_compact_csv", "Zelle payment from a friend 123456789", 5000)
        is None
    )
