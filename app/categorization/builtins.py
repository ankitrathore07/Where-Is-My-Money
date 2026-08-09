"""Immutable exact-match built-in category and merchant catalog."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from app.categorization.normalization import MAX_MERCHANT_LENGTH, merchant_key

AmountDirection = Literal["expense", "income", "either"]

BUILTIN_CATEGORY_DEFINITIONS: Final = (
    ("Income", "income"),
    ("Transfers", "transfer"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Groceries", "expense"),
    ("Dining & Drinks", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Software & Online Services", "expense"),
    ("Health & Fitness", "expense"),
    ("Insurance", "expense"),
    ("Education", "expense"),
    ("Travel", "expense"),
    ("Personal Care", "expense"),
    ("Pets", "expense"),
    ("Childcare", "expense"),
    ("Gifts & Donations", "expense"),
    ("Taxes & Fees", "expense"),
    ("Cash & ATM", "expense"),
    ("Uncategorized", "expense"),
)


@dataclass(frozen=True)
class BuiltinMerchantRule:
    """Map exact canonical merchant keys to one review-friendly decision."""

    merchant_keys: tuple[str, ...]
    normalized_merchant: str
    category_name: str
    is_subscription: bool = False
    amount_direction: AmountDirection = "expense"


def _rule(
    key: str,
    display: str,
    category: str,
    is_subscription: bool = False,
    amount_direction: AmountDirection = "expense",
) -> BuiltinMerchantRule:
    return BuiltinMerchantRule((key,), display, category, is_subscription, amount_direction)


BUILTIN_MERCHANT_RULES: Final = (
    # Income and transfers
    _rule("PAYROLL", "Payroll", "Income", amount_direction="income"),
    _rule("DIRECT DEPOSIT PAYROLL", "Payroll", "Income", amount_direction="income"),
    _rule("INTEREST PAYMENT", "Interest", "Income", amount_direction="income"),
    _rule("INTERNAL TRANSFER", "Internal Transfer", "Transfers", amount_direction="either"),
    _rule("ONLINE TRANSFER", "Account Transfer", "Transfers", amount_direction="either"),
    _rule("CREDIT CARD PAYMENT", "Credit Card Payment", "Transfers"),
    # Housing and utilities
    _rule("RENT PAYMENT", "Rent", "Housing"),
    _rule("MORTGAGE PAYMENT", "Mortgage", "Housing"),
    _rule("HOA PAYMENT", "HOA", "Housing"),
    _rule("COMED", "ComEd", "Utilities"),
    _rule("XFINITY", "Xfinity", "Utilities"),
    _rule("AT T WIRELESS", "AT&T", "Utilities"),
    _rule("VERIZON WIRELESS", "Verizon", "Utilities"),
    _rule("T MOBILE", "T-Mobile", "Utilities"),
    # Groceries
    _rule("WHOLE FOODS MARKET", "Whole Foods", "Groceries"),
    _rule("TRADER JOE S", "Trader Joe's", "Groceries"),
    _rule("ALDI", "Aldi", "Groceries"),
    _rule("KROGER", "Kroger", "Groceries"),
    _rule("SAFEWAY", "Safeway", "Groceries"),
    _rule("PUBLIX", "Publix", "Groceries"),
    _rule("H E B", "H-E-B", "Groceries"),
    _rule("WEGMANS", "Wegmans", "Groceries"),
    _rule("INSTACART", "Instacart", "Groceries"),
    _rule("INSTACART PLUS", "Instacart+", "Groceries", True),
    _rule("HELLOFRESH", "HelloFresh", "Groceries", True),
    # Dining and drinks
    _rule("MCDONALD S", "McDonald's", "Dining & Drinks"),
    _rule("STARBUCKS", "Starbucks", "Dining & Drinks"),
    _rule("CHIPOTLE", "Chipotle", "Dining & Drinks"),
    _rule("CHICK FIL A", "Chick-fil-A", "Dining & Drinks"),
    _rule("PANERA BREAD", "Panera Bread", "Dining & Drinks"),
    _rule("DOMINO S", "Domino's", "Dining & Drinks"),
    _rule("DOORDASH", "DoorDash", "Dining & Drinks"),
    _rule("DOORDASH DASHPASS", "DashPass", "Dining & Drinks", True),
    _rule("UBER EATS", "Uber Eats", "Dining & Drinks"),
    _rule("UBER ONE", "Uber One", "Dining & Drinks", True),
    _rule("GRUBHUB", "Grubhub", "Dining & Drinks"),
    # Transportation
    _rule("UBER TRIP", "Uber", "Transportation"),
    _rule("LYFT RIDE", "Lyft", "Transportation"),
    _rule("SHELL", "Shell", "Transportation"),
    _rule("EXXON", "Exxon", "Transportation"),
    _rule("CHEVRON", "Chevron", "Transportation"),
    _rule("BP", "BP", "Transportation"),
    _rule("CTA VENTRA", "CTA", "Transportation"),
    _rule("PARKING", "Parking", "Transportation"),
    _rule("TOLL PAYMENT", "Tolls", "Transportation"),
    # Shopping
    _rule("AMAZON", "Amazon", "Shopping"),
    _rule("TARGET", "Target", "Shopping"),
    _rule("WALMART", "Walmart", "Shopping"),
    _rule("COSTCO", "Costco", "Shopping"),
    _rule("AMAZON PRIME", "Amazon Prime", "Shopping", True),
    _rule("BEST BUY", "Best Buy", "Shopping"),
    _rule("HOME DEPOT", "Home Depot", "Shopping"),
    _rule("LOWE S", "Lowe's", "Shopping"),
    _rule("MACY S", "Macy's", "Shopping"),
    _rule("IKEA", "IKEA", "Shopping"),
    # Entertainment
    _rule("NETFLIX COM", "Netflix", "Entertainment", True),
    _rule("SPOTIFY USA", "Spotify", "Entertainment", True),
    _rule("HULU", "Hulu", "Entertainment", True),
    _rule("DISNEY PLUS", "Disney+", "Entertainment", True),
    _rule("MAX COM", "Max", "Entertainment", True),
    _rule("PARAMOUNT PLUS", "Paramount+", "Entertainment", True),
    _rule("PEACOCK TV", "Peacock", "Entertainment", True),
    _rule("YOUTUBE PREMIUM", "YouTube Premium", "Entertainment", True),
    _rule("YOUTUBE TV", "YouTube TV", "Entertainment", True),
    _rule("APPLE MUSIC", "Apple Music", "Entertainment", True),
    _rule("AUDIBLE", "Audible", "Entertainment", True),
    _rule("KINDLE UNLIMITED", "Kindle Unlimited", "Entertainment", True),
    _rule("AMC THEATRES", "AMC Theatres", "Entertainment"),
    _rule("STEAM GAMES", "Steam", "Entertainment"),
    # Software and online services
    _rule("MICROSOFT 365", "Microsoft 365", "Software & Online Services", True),
    _rule("ADOBE CREATIVE CLOUD", "Adobe Creative Cloud", "Software & Online Services", True),
    _rule("DROPBOX", "Dropbox", "Software & Online Services", True),
    _rule("GOOGLE ONE", "Google One", "Software & Online Services", True),
    _rule("ICLOUD PLUS", "iCloud+", "Software & Online Services", True),
    _rule("ONEPASSWORD", "1Password", "Software & Online Services", True),
    _rule("NORDVPN", "NordVPN", "Software & Online Services", True),
    _rule("GITHUB COPILOT", "GitHub Copilot", "Software & Online Services", True),
    # Health and fitness
    _rule("CVS PHARMACY", "CVS Pharmacy", "Health & Fitness"),
    _rule("WALGREENS", "Walgreens", "Health & Fitness"),
    _rule("PLANET FITNESS", "Planet Fitness", "Health & Fitness", True),
    _rule("CLASS PASS", "ClassPass", "Health & Fitness", True),
    _rule("PELOTON MEMBERSHIP", "Peloton", "Health & Fitness", True),
    # Insurance
    _rule("STATE FARM", "State Farm", "Insurance"),
    _rule("GEICO", "GEICO", "Insurance"),
    _rule("PROGRESSIVE", "Progressive", "Insurance"),
    _rule("ALLSTATE", "Allstate", "Insurance"),
    # Education
    _rule("COURSERA PLUS", "Coursera Plus", "Education", True),
    _rule("UDEMY", "Udemy", "Education"),
    _rule("CHEGG STUDY", "Chegg Study", "Education", True),
    # Travel
    _rule("UNITED AIRLINES", "United Airlines", "Travel"),
    _rule("AMERICAN AIRLINES", "American Airlines", "Travel"),
    _rule("DELTA AIR LINES", "Delta Air Lines", "Travel"),
    _rule("SOUTHWEST AIRLINES", "Southwest Airlines", "Travel"),
    _rule("AIRBNB", "Airbnb", "Travel"),
    _rule("MARRIOTT", "Marriott", "Travel"),
    _rule("HILTON", "Hilton", "Travel"),
    _rule("HERTZ", "Hertz", "Travel"),
    _rule("ENTERPRISE RENT A CAR", "Enterprise", "Travel"),
    # Pets
    _rule("PETCO", "Petco", "Pets"),
    _rule("PETSMART", "PetSmart", "Pets"),
    _rule("CHEWY", "Chewy", "Pets"),
    # Taxes, fees, and cash
    _rule("IRS US TAX PAYMENT", "IRS", "Taxes & Fees"),
    _rule("BANK SERVICE FEE", "Bank Fee", "Taxes & Fees"),
    _rule("OVERDRAFT FEE", "Overdraft Fee", "Taxes & Fees"),
    _rule("ATM FEE", "ATM Fee", "Taxes & Fees"),
    _rule("ATM WITHDRAWAL", "ATM Withdrawal", "Cash & ATM"),
)


def _build_rule_lookup(
    rules: tuple[BuiltinMerchantRule, ...],
) -> MappingProxyType[str, BuiltinMerchantRule]:
    """Validate and flatten immutable catalog rules into an exact-key lookup."""
    category_names = {name for name, _ in BUILTIN_CATEGORY_DEFINITIONS}
    lookup: dict[str, BuiltinMerchantRule] = {}
    for rule in rules:
        if rule.category_name not in category_names:
            raise ValueError(f"unknown category: {rule.category_name}")
        if rule.amount_direction not in {"expense", "income", "either"}:
            raise ValueError(f"invalid direction: {rule.amount_direction}")
        if type(rule.is_subscription) is not bool:
            raise ValueError("subscription must be a boolean")
        if not rule.normalized_merchant.strip():
            raise ValueError("display merchant must not be blank")
        if not rule.merchant_keys:
            raise ValueError("merchant keys must not be blank")
        for key in rule.merchant_keys:
            if not key:
                raise ValueError("merchant key must not be blank")
            if len(key) > MAX_MERCHANT_LENGTH or merchant_key(key) != key:
                raise ValueError(f"merchant key must be canonical: {key}")
            if key in lookup:
                raise ValueError(f"duplicate merchant key: {key}")
            lookup[key] = rule
    return MappingProxyType(lookup)


_BUILTIN_RULE_LOOKUP: Final = _build_rule_lookup(BUILTIN_MERCHANT_RULES)


def find_builtin_rule(merchant_key: str) -> BuiltinMerchantRule | None:
    """Return a rule only when its already-normalized key matches exactly."""
    return _BUILTIN_RULE_LOOKUP.get(merchant_key)
