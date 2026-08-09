"""Pure merchant text normalization used by imports and saved rules."""

import unicodedata

MAX_MERCHANT_LENGTH = 255


def merchant_key(description: str) -> str:
    """Return the exact comparison key for a statement description."""
    normalized = unicodedata.normalize("NFKC", description).strip().upper()
    key_characters: list[str] = []

    for character in normalized:
        if character.isalnum():
            key_characters.append(character)
        elif key_characters and key_characters[-1] != " ":
            key_characters.append(" ")

    return "".join(key_characters).strip()


def merchant_display_fallback(description: str) -> str:
    """Return a compact merchant label when no rule supplies one."""
    return " ".join(description.split())[:MAX_MERCHANT_LENGTH]
