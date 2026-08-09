"""Google sign-in, callback, and sign-out routes."""

from collections.abc import Mapping
from typing import Annotated

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.service import (
    GoogleIdentityConflict,
    InvalidGoogleIdentity,
    get_or_create_google_user,
)
from app.core.middleware import require_csrf
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["authentication"])


def _enforce_rate_limit(request: Request) -> None:
    client_host = request.client.host if request.client is not None else "unknown"
    key = f"{request.url.path}:{client_host}"
    if not request.app.state.auth_rate_limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts; try again shortly",
        )


@router.post("/google", dependencies=[Depends(require_csrf)])
async def google_sign_in(request: Request) -> RedirectResponse:
    """Begin Google sign-in after CSRF and local rate-limit checks."""
    _enforce_rate_limit(request)
    oauth = request.app.state.google_oauth
    if oauth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    """Finish OpenID Connect and establish a fresh signed session."""
    _enforce_rate_limit(request)
    oauth = request.app.state.google_oauth
    if oauth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )

    try:
        token = await oauth.google.authorize_access_token(request)
        raw_claims = token.get("userinfo", {})
        claims = raw_claims if isinstance(raw_claims, Mapping) else {}
        user = get_or_create_google_user(session, claims)
        session.commit()
    except InvalidGoogleIdentity as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return a verified identity",
        ) from exc
    except GoogleIdentityConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That verified email belongs to another account",
        ) from exc
    except OAuthError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google sign-in could not be completed",
        ) from exc

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/workspaces", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sign-out", dependencies=[Depends(require_csrf)])
async def sign_out(request: Request) -> RedirectResponse:
    """Destroy all signed-session state in the browser."""
    _enforce_rate_limit(request)
    request.session.clear()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
