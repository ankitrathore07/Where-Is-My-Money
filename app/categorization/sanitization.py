"""Privacy-preserving transaction description sanitization."""

import re
import unicodedata

MAX_AI_DESCRIPTION_LENGTH = 160
_TERMINAL_REFERENCE = re.compile(r"(?:\s+\d{6,})+$")
_LONG_IDENTIFIER = re.compile(r"\b\d{6,}\b")
_ACH_ID_SUFFIX = re.compile(r"\s+(?:WEB|PPD|CCD|CTX) ID:\s+\S+\s*$")
_ZELLE_TO = re.compile(r"^ZELLE PAYMENT TO\s+.+$")
_ZELLE_FROM = re.compile(r"^ZELLE PAYMENT FROM\s+.+$")


def _canonical_chase_description(description: str) -> str | None:
    if re.fullmatch(r"CAPITAL ONE MOBILE PMT(?: [A-Z0-9]+)?", description):
        return "CAPITAL ONE MOBILE PMT"
    if re.fullmatch(r"CITI CARD ONLINE PAYMENT(?: [A-Z0-9]+)?", description):
        return "CITI CARD ONLINE PAYMENT"
    best_buy = re.fullmatch(
        r"BEST BUY (AUTO (?:PYMT|PAYMENT)|PAYMENT)(?: [A-Z0-9]+)?",
        description,
    )
    if best_buy:
        return f"BEST BUY {best_buy.group(1)}"
    if description in {"NEWREZ-SHELLPOIN ACH PMT", "NEWREZ-SHELLPOINT ACH PMT"}:
        return description
    if description == "MICROSOFT EDIPAYMENT":
        return description
    if description == "MICROSOFT CTX" or re.fullmatch(r"MICROSOFT(?: \d{6,}){2}", description):
        return "MICROSOFT CTX"
    if re.fullmatch(r"XOOM DEBIT(?: OID \d+)?", description):
        return "XOOM DEBIT"
    if re.fullmatch(r"REMOTE ONLINE DEPOSIT(?: # \d+)?", description):
        return "REMOTE ONLINE DEPOSIT"
    return None


def sanitize_transaction_description(description: str) -> str:
    """Remove reference and party identifiers before rules or network use."""
    normalized = unicodedata.normalize("NFKC", description)
    printable = "".join(
        character if not unicodedata.category(character).startswith("C") else " "
        for character in normalized
    )
    compact = " ".join(printable.upper().split())
    without_ach_id = _ACH_ID_SUFFIX.sub("", compact).strip()
    if _ZELLE_TO.fullmatch(without_ach_id):
        return "ZELLE PAYMENT TO <PAYEE>"
    if _ZELLE_FROM.fullmatch(without_ach_id):
        return "ZELLE PAYMENT FROM <PAYER>"
    canonical = _canonical_chase_description(without_ach_id)
    if canonical is not None:
        return canonical
    without_terminal_reference = _TERMINAL_REFERENCE.sub("", without_ach_id).strip()
    if _ZELLE_TO.fullmatch(without_terminal_reference):
        return "ZELLE PAYMENT TO <PAYEE>"
    if _ZELLE_FROM.fullmatch(without_terminal_reference):
        return "ZELLE PAYMENT FROM <PAYER>"
    canonical = _canonical_chase_description(without_terminal_reference)
    if canonical is not None:
        return canonical
    redacted = _LONG_IDENTIFIER.sub("<ID>", without_terminal_reference)
    return redacted[:MAX_AI_DESCRIPTION_LENGTH].strip()
