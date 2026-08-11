import asyncio

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.middleware import UploadBodyLimitMiddleware
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


@pytest.mark.parametrize(
    "path,message",
    [
        ("/workspaces/1/payslips", b"Payslip upload is too large."),
        ("/workspaces/1/document-uploads", b"Document upload is too large."),
    ],
)
def test_upload_body_limit_counts_streamed_chunks_without_content_length(
    path: str, message: bytes
) -> None:
    completed_downstream = False

    async def consuming_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal completed_downstream
        more_body = True
        while more_body:
            message = await receive()
            more_body = message.get("more_body", False)
        completed_downstream = True

    middleware = UploadBodyLimitMiddleware(
        consuming_app,
        max_file_bytes=5,
        multipart_overhead_bytes=0,
    )
    incoming: list[Message] = [
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope = upload_scope(path)
    asyncio.run(middleware(scope, receive, send))

    assert completed_downstream is False
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == message


def upload_scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
