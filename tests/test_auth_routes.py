from collections.abc import Generator, Mapping

import pytest
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import SlidingWindowRateLimiter
from app.db.models import Base, User, Workspace, WorkspaceMembership
from app.db.session import get_db
from app.main import create_app


class FakeGoogleClient:
    def __init__(self, claims: Mapping[str, object]) -> None:
        self.claims = claims

    async def authorize_redirect(self, request, redirect_uri: str) -> RedirectResponse:
        request.session["_fake_oauth_state"] = "stored"
        return RedirectResponse("https://accounts.example.test/authorize", status_code=302)

    async def authorize_access_token(self, request) -> dict[str, object]:
        assert request.session.get("_fake_oauth_state") == "stored"
        return {
            "access_token": "external-token-never-persisted",
            "token_type": "Bearer",
            "expires_in": 3600,
            "userinfo": dict(self.claims),
        }


class FakeOAuth:
    def __init__(self, claims: Mapping[str, object]) -> None:
        self.google = FakeGoogleClient(claims)


class FakeMalformedGoogleClient(FakeGoogleClient):
    async def authorize_access_token(self, request) -> dict[str, object]:
        assert request.session.get("_fake_oauth_state") == "stored"
        return {"userinfo": None}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def db_factory() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def verified_claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "sub": "route-user-sub",
        "email": "route-user@example.test",
        "email_verified": True,
        "name": "Route User",
    }
    claims.update(overrides)
    return claims


def build_test_app(
    db_factory: sessionmaker[Session],
    *,
    claims: Mapping[str, object] | None = None,
    configured: Settings | None = None,
    include_fake_oauth: bool = True,
):
    test_settings = configured or Settings(
        _env_file=None,
        app_env="development",
        secret_key="test-session-secret-that-is-long-enough",
        database_url="sqlite://",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
    )
    oauth = FakeOAuth(claims or verified_claims()) if include_fake_oauth else None
    application = create_app(test_settings, google_oauth=oauth)

    def override_db() -> Generator[Session, None, None]:
        with db_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    return application


async def csrf_token(client: AsyncClient) -> str:
    response = await client.get("/")
    assert response.status_code == 200
    return client.cookies["wimm_csrf"]


async def complete_sign_in(client: AsyncClient) -> None:
    token = await csrf_token(client)
    started = await client.post(
        "/auth/google",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert started.status_code == 302
    callback = await client.get("/auth/google/callback", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/workspaces"


@pytest.mark.anyio
async def test_home_issues_signed_csrf_cookie(db_factory: sessionmaker[Session]) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")

    cookie = response.headers["set-cookie"]
    assert "wimm_csrf=" in cookie
    assert "Max-Age=3600" in cookie
    assert "SameSite=lax" in cookie


@pytest.mark.anyio
async def test_google_sign_in_requires_csrf(db_factory: sessionmaker[Session]) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/auth/google")

    assert response.status_code == 403


@pytest.mark.anyio
async def test_google_sign_in_redirects_with_valid_csrf(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        token = await csrf_token(client)
        response = await client.post(
            "/auth/google",
            data={"csrf_token": token},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://accounts.example.test/authorize"


@pytest.mark.anyio
async def test_callback_creates_session_and_private_workspace(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        await complete_sign_in(client)
        home = await client.get("/")

    assert "Route User" in home.text
    with db_factory() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert session.scalar(select(func.count()).select_from(WorkspaceMembership)) == 1


@pytest.mark.anyio
async def test_unverified_callback_does_not_create_session_or_user(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory, claims=verified_claims(email_verified=False))

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        token = await csrf_token(client)
        await client.post("/auth/google", data={"csrf_token": token})
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 400
    assert "external-token" not in response.text
    with db_factory() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.anyio
async def test_malformed_provider_userinfo_returns_safe_error(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)
    application.state.google_oauth.google = FakeMalformedGoogleClient(verified_claims())

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        token = await csrf_token(client)
        await client.post("/auth/google", data={"csrf_token": token})
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 400
    with db_factory() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.anyio
async def test_tampered_session_cookie_is_not_authenticated(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        await complete_sign_in(client)
        signed_cookie = client.cookies["wimm_session"]
        client.cookies.clear()
        client.cookies.set("wimm_session", f"{signed_cookie}tampered")
        response = await client.get("/")

    assert "Route User" not in response.text


@pytest.mark.anyio
async def test_sign_out_requires_csrf_and_clears_authentication(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        await complete_sign_in(client)
        rejected = await client.post("/auth/sign-out", follow_redirects=False)
        assert rejected.status_code == 403

        token = await csrf_token(client)
        response = await client.post(
            "/auth/sign-out",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        home = await client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "Route User" not in home.text


@pytest.mark.anyio
async def test_missing_google_configuration_returns_safe_error(
    db_factory: sessionmaker[Session],
) -> None:
    configured = Settings(
        _env_file=None,
        app_env="development",
        secret_key="test-session-secret-that-is-long-enough",
        google_client_id="",
        google_client_secret="",
    )
    application = build_test_app(
        db_factory,
        configured=configured,
        include_fake_oauth=False,
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        token = await csrf_token(client)
        response = await client.post("/auth/google", data={"csrf_token": token})

    assert response.status_code == 503
    assert "client_secret" not in response.text


@pytest.mark.anyio
async def test_production_session_cookie_is_http_only_same_site_and_secure(
    db_factory: sessionmaker[Session],
) -> None:
    configured = Settings(
        _env_file=None,
        app_env="production",
        secret_key="production-session-secret-that-is-long-enough",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
    )
    application = build_test_app(db_factory, configured=configured)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://testserver",
    ) as client:
        await complete_sign_in(client)
        cookie_headers = client.cookies.jar

    session_cookie = next(cookie for cookie in cookie_headers if cookie.name == "wimm_session")
    assert session_cookie.secure is True
    assert session_cookie.has_nonstandard_attr("httponly")
    assert session_cookie.get_nonstandard_attr("samesite") == "lax"


@pytest.mark.anyio
async def test_auth_rate_limit_returns_429(db_factory: sessionmaker[Session]) -> None:
    application = build_test_app(db_factory)
    application.state.auth_rate_limiter = SlidingWindowRateLimiter(
        limit=1,
        window_seconds=60,
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        token = await csrf_token(client)
        first = await client.post("/auth/google", data={"csrf_token": token})
        second = await client.post("/auth/google", data={"csrf_token": token})

    assert first.status_code == 302
    assert second.status_code == 429
