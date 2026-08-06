import secrets


def generate_invite_token(length: int = 48) -> str:
    """Generate a URL-safe token for workspace invitations."""
    return secrets.token_urlsafe(length)[:length]


def constant_time_compare(a: str | None, b: str | None) -> bool:
    """Compare two strings in constant time to avoid timing attacks."""
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)
