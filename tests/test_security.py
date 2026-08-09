from app.core.security import (
    SlidingWindowRateLimiter,
    create_csrf_token,
    hash_invitation_token,
    validate_csrf_token,
)


def test_fresh_csrf_token_validates_against_cookie() -> None:
    token = create_csrf_token("test-secret")

    assert validate_csrf_token("test-secret", token, token)


def test_csrf_rejects_missing_or_altered_submission() -> None:
    token = create_csrf_token("test-secret")

    assert not validate_csrf_token("test-secret", None, token)
    assert not validate_csrf_token("test-secret", token, None)
    assert not validate_csrf_token("test-secret", token, f"{token}altered")


def test_csrf_rejects_token_signed_with_another_secret() -> None:
    token = create_csrf_token("first-secret")

    assert not validate_csrf_token("second-secret", token, token)


def test_csrf_rejects_expired_token() -> None:
    token = create_csrf_token("test-secret")

    assert not validate_csrf_token("test-secret", token, token, max_age=-1)


def test_invitation_token_uses_known_sha256_digest() -> None:
    assert hash_invitation_token("invite-secret") == (
        "2a1ed5f04ebb12c50d33ea3031b46260a6d503e72c1d992b2fd3d9e048cd5c8f"
    )


def test_rate_limiter_releases_attempts_after_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)

    assert limiter.allow("client", now=0)
    assert limiter.allow("client", now=1)
    assert not limiter.allow("client", now=2)
    assert limiter.allow("client", now=61)


def test_rate_limiter_keeps_client_windows_separate() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)

    assert limiter.allow("first", now=0)
    assert not limiter.allow("first", now=1)
    assert limiter.allow("second", now=1)
