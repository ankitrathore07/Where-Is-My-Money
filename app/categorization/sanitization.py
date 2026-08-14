"""Privacy-preserving transaction description sanitization."""

import re
import unicodedata

MAX_AI_DESCRIPTION_LENGTH = 160
_TERMINAL_REFERENCE = re.compile(r"(?:\s+\d{6,})+$")
_LONG_IDENTIFIER = re.compile(r"\b\d{6,}\b")
_ZELLE_TO = re.compile(r"^ZELLE PAYMENT TO\s+.+$")
_ZELLE_FROM = re.compile(r"^ZELLE PAYMENT FROM\s+.+$")


def sanitize_transaction_description(description: str) -> str:
    """Remove reference and party identifiers before rules or network use."""
    normalized = unicodedata.normalize("NFKC", description)
    printable = "".join(
        character if not unicodedata.category(character).startswith("C") else " "
        for character in normalized
    )
    compact = " ".join(printable.upper().split())
    without_terminal_reference = _TERMINAL_REFERENCE.sub("", compact).strip()
    if _ZELLE_TO.fullmatch(without_terminal_reference):
        return "ZELLE PAYMENT TO <PAYEE>"
    if _ZELLE_FROM.fullmatch(without_terminal_reference):
        return "ZELLE PAYMENT FROM <PAYER>"
    redacted = _LONG_IDENTIFIER.sub("<ID>", without_terminal_reference)
    return redacted[:MAX_AI_DESCRIPTION_LENGTH].strip()
