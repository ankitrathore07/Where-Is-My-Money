import asyncio

import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
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


@pytest.mark.parametrize(
    "path,message",
    [
        ("/workspaces/-1/payslips", "Payslip upload is too large."),
        ("/workspaces/not-an-integer/payslips", "Payslip upload is too large."),
        ("/workspaces/-1/document-uploads", "Document upload is too large."),
        ("/workspaces/not-an-integer/document-uploads", "Document upload is too large."),
    ],
)
def test_upload_body_limit_rejects_any_workspace_segment_before_multipart_parsing(
    path: str,
    message: str,
) -> None:
    downstream_invoked = False
    multipart_parsed = False
    application = FastAPI()

    @application.post("/workspaces/{workspace_id}/{upload_route}")
    async def parse_upload(request: Request, workspace_id: str, upload_route: str) -> Response:
        nonlocal downstream_invoked, multipart_parsed
        downstream_invoked = True
        await request.form()
        multipart_parsed = True
        return Response(status_code=204)

    application.add_middleware(
        UploadBodyLimitMiddleware,
        max_file_bytes=32,
        multipart_overhead_bytes=0,
    )

    async def post_oversized_multipart() -> tuple[int, str]:
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                path,
                files={"document": ("synthetic.pdf", b"x" * 64, "application/pdf")},
            )
        return response.status_code, response.text

    status_code, response_text = asyncio.run(post_oversized_multipart())

    assert (status_code, response_text) == (413, message)
    assert downstream_invoked is False
    assert multipart_parsed is False


def test_upload_body_limit_does_not_match_extra_path_segments() -> None:
    downstream_invoked = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal downstream_invoked
        downstream_invoked = True

    middleware = UploadBodyLimitMiddleware(
        downstream,
        max_file_bytes=1,
        multipart_overhead_bytes=0,
    )
    scope = upload_scope("/workspaces/1/document-uploads/extra")

    async def receive() -> Message:
        return {"type": "http.request", "body": b"oversized", "more_body": False}

    async def send(message: Message) -> None:
        raise AssertionError(f"unexpected response: {message}")

    asyncio.run(middleware(scope, receive, send))

    assert downstream_invoked is True


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
