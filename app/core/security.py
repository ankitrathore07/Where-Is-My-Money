import hashlib
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

CSRF_SALT = "where-is-my-money-csrf"


def generate_invite_token(length: int = 48) -> str:
    """Generate a URL-safe token for workspace invitations."""
    return secrets.token_urlsafe(length)[:length]


def constant_time_compare(a: str | None, b: str | None) -> bool:
    """Compare two strings in constant time to avoid timing attacks."""
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)


def create_csrf_token(secret_key: str) -> str:
    """Create a signed token suitable for a double-submit CSRF cookie."""
    serializer = URLSafeTimedSerializer(secret_key, salt=CSRF_SALT)
    return serializer.dumps({"nonce": secrets.token_urlsafe(32)})


def validate_csrf_token(
    secret_key: str,
    cookie_token: str | None,
    submitted_token: str | None,
    *,
    max_age: int = 3600,
) -> bool:
    """Validate equality, signature, and age for a double-submit token."""
    if not constant_time_compare(cookie_token, submitted_token):
        return False

    serializer = URLSafeTimedSerializer(secret_key, salt=CSRF_SALT)
    try:
        serializer.loads(cookie_token, max_age=max_age)
    except (BadSignature, SignatureExpired, TypeError):
        return False
    return True


def hash_invitation_token(raw_token: str) -> str:
    """Return the one-way digest stored for an invitation bearer token."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


class SlidingWindowRateLimiter:
    """A process-local fixed-capacity limiter over a sliding time window."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record an allowed attempt or reject when the key is at capacity."""
        current_time = time.monotonic() if now is None else now
        with self._lock:
            attempts = self._attempts[key]
            while attempts and current_time - attempts[0] >= self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                return False
            attempts.append(current_time)
            return True
