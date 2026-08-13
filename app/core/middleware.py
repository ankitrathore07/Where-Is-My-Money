"""Browser security middleware and CSRF enforcement."""

import logging
import re
import time
import uuid

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.security import create_csrf_token, validate_csrf_token

CSRF_COOKIE_NAME = "wimm_csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_MAX_AGE = 3600
BOUNDED_UPLOAD_PATH = re.compile(r"/workspaces/[^/]+/(?:payslips|document-uploads)")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
request_logger = logging.getLogger("where_is_my_money.request")


def safe_request_path(request: Request) -> str:
    """Return a route template without logging arbitrary unmatched path text."""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str):
        return route_path
    return "[unmatched]"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a correlation ID, safe response headers, and one request log event."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        supplied_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_id if REQUEST_ID_PATTERN.fullmatch(supplied_id) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        request_logger.info(
            "request.completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": safe_request_path(request),
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response


class _UploadBodyTooLarge(Exception):
    """Stop multipart parsing once a bounded upload request is too large."""


class UploadBodyLimitMiddleware:
    """Bound supported upload bodies before Starlette spools uploaded files."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_file_bytes: int,
        multipart_overhead_bytes: int = 64 * 1024,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_file_bytes + multipart_overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._targets_upload(scope):
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is None or content_length <= self.max_body_bytes:
            received_bytes = 0

            async def limited_receive() -> Message:
                nonlocal received_bytes
                message = await receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > self.max_body_bytes:
                        raise _UploadBodyTooLarge
                return message

            try:
                await self.app(scope, limited_receive, send)
            except _UploadBodyTooLarge:
                await self._reject(scope, receive, send)
            return

        await self._reject(scope, receive, send)

    @staticmethod
    def _targets_upload(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and BOUNDED_UPLOAD_PATH.fullmatch(scope.get("path", "")) is not None
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        payslip_route = scope.get("path", "").endswith("/payslips")
        message = (
            "Payslip upload is too large." if payslip_route else "Document upload is too large."
        )
        response = PlainTextResponse(message, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        await response(scope, receive, send)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Maintain the signed cookie half of double-submit CSRF protection."""

    def __init__(self, app, *, configured: Settings) -> None:
        super().__init__(app)
        self.secret_key = configured.secret_key or ""
        self.https_only = configured.session_https_only

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        issue_cookie = not validate_csrf_token(
            self.secret_key,
            cookie_token,
            cookie_token,
            max_age=CSRF_MAX_AGE,
        )
        token = create_csrf_token(self.secret_key) if issue_cookie else cookie_token
        request.state.csrf_token = token
        response = await call_next(request)
        if issue_cookie:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                token,
                max_age=CSRF_MAX_AGE,
                secure=self.https_only,
                httponly=False,
                samesite="lax",
                path="/",
            )
        return response


async def require_csrf(request: Request) -> None:
    """Reject a mutation without a matching, signed, fresh CSRF token."""
    submitted_token = request.headers.get(CSRF_HEADER_NAME)
    if submitted_token is None:
        form = await request.form()
        form_token = form.get(CSRF_FORM_FIELD)
        submitted_token = form_token if isinstance(form_token, str) else None

    configured: Settings = request.app.state.settings
    if not validate_csrf_token(
        configured.secret_key or "",
        request.cookies.get(CSRF_COOKIE_NAME),
        submitted_token,
        max_age=CSRF_MAX_AGE,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request could not be verified",
        )
