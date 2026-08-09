"""Browser security middleware and CSRF enforcement."""

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Settings
from app.core.security import create_csrf_token, validate_csrf_token

CSRF_COOKIE_NAME = "wimm_csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_MAX_AGE = 3600


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
