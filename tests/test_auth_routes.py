import re
from collections.abc import Generator, Mapping
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import SlidingWindowRateLimiter
from app.db.models import Base, User, Workspace, WorkspaceInvitation, WorkspaceMembership
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


@pytest.mark.anyio
async def test_signed_out_workspace_list_redirects(db_factory: sessionmaker[Session]) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/workspaces", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_personal_workspace_routes_are_isolated(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with (
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as alex_client,
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as blair_client,
    ):
        application.state.google_oauth.google.claims = verified_claims(
            sub="alex-route-sub",
            email="alex-route@example.test",
            name="Alex Route",
        )
        await complete_sign_in(alex_client)
        application.state.google_oauth.google.claims = verified_claims(
            sub="blair-route-sub",
            email="blair-route@example.test",
            name="Blair Route",
        )
        await complete_sign_in(blair_client)

        with db_factory() as session:
            alex = session.scalar(select(User).where(User.google_sub == "alex-route-sub"))
            blair = session.scalar(select(User).where(User.google_sub == "blair-route-sub"))
            assert alex is not None
            assert blair is not None
            alex_personal_id = alex.owned_workspaces[0].id
            blair_personal_id = blair.owned_workspaces[0].id

        alex_own = await alex_client.get(f"/workspaces/{alex_personal_id}")
        alex_foreign = await alex_client.get(f"/workspaces/{blair_personal_id}")
        blair_own = await blair_client.get(f"/workspaces/{blair_personal_id}")
        blair_foreign = await blair_client.get(f"/workspaces/{alex_personal_id}")

    assert alex_own.status_code == 200
    assert blair_own.status_code == 200
    assert alex_foreign.status_code == 404
    assert blair_foreign.status_code == 404


@pytest.mark.anyio
async def test_household_creation_requires_csrf_and_grants_creator_access(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        await complete_sign_in(client)
        rejected = await client.post("/workspaces", data={"name": "Shared Home"})
        token = await csrf_token(client)
        created = await client.post(
            "/workspaces",
            data={"name": "  Shared Home  ", "csrf_token": token},
            follow_redirects=False,
        )
        detail = await client.get(created.headers["location"])

    assert rejected.status_code == 403
    assert created.status_code == 303
    assert re.fullmatch(r"/workspaces/\d+", created.headers["location"])
    assert detail.status_code == 200
    assert "Shared Home" in detail.text
    assert "Route User" in detail.text
    assert "Import CSV" not in detail.text
    assert "Categorize transactions" not in detail.text


@pytest.mark.anyio
async def test_household_invitation_is_email_bound_one_time_and_equal_access(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with (
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as owner_client,
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as wrong_client,
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as invitee_client,
    ):
        application.state.google_oauth.google.claims = verified_claims(
            sub="owner-route-sub",
            email="owner-route@example.test",
            name="Owner Route",
        )
        await complete_sign_in(owner_client)
        owner_csrf = await csrf_token(owner_client)
        created = await owner_client.post(
            "/workspaces",
            data={"name": "Shared Home", "csrf_token": owner_csrf},
            follow_redirects=False,
        )
        household_id = int(created.headers["location"].rsplit("/", 1)[1])

        invited = await owner_client.post(
            f"/workspaces/{household_id}/invitations",
            data={"email": "Invitee@Example.Test", "csrf_token": owner_csrf},
        )
        match = re.search(r"/invitations/([A-Za-z0-9_-]+)", invited.text)
        assert match is not None
        raw_token = match.group(1)
        assert f'href="/invitations/{raw_token}"' in invited.text
        assert f"http://testserver/invitations/{raw_token}" not in invited.text

        with db_factory() as session:
            stored_invitation = session.scalar(
                select(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == household_id)
            )
            assert stored_invitation is not None
            assert stored_invitation.token != raw_token
            assert len(stored_invitation.token) == 64

        application.state.google_oauth.google.claims = verified_claims(
            sub="wrong-route-sub",
            email="wrong@example.test",
            name="Wrong Person",
        )
        await complete_sign_in(wrong_client)
        wrong_csrf = await csrf_token(wrong_client)
        mismatched = await wrong_client.post(
            f"/invitations/{raw_token}/accept",
            data={"csrf_token": wrong_csrf},
            follow_redirects=False,
        )

        application.state.google_oauth.google.claims = verified_claims(
            sub="invitee-route-sub",
            email="invitee@example.test",
            name="Invitee Route",
        )
        await complete_sign_in(invitee_client)
        pending = await invitee_client.get("/workspaces")
        invitation_page = await invitee_client.get(f"/invitations/{raw_token}")
        invitee_csrf = await csrf_token(invitee_client)
        accepted = await invitee_client.post(
            f"/invitations/{raw_token}/accept",
            data={"csrf_token": invitee_csrf},
            follow_redirects=False,
        )
        shared_detail = await invitee_client.get(f"/workspaces/{household_id}")
        accepted_again = await invitee_client.post(
            f"/invitations/{raw_token}/accept",
            data={"csrf_token": invitee_csrf},
            follow_redirects=False,
        )
        member_invite = await invitee_client.post(
            f"/workspaces/{household_id}/invitations",
            data={"email": "next@example.test", "csrf_token": invitee_csrf},
        )

    assert invited.status_code == 201
    assert mismatched.status_code == 400
    assert "Shared Home" in pending.text
    assert "Shared Home" in invitation_page.text
    assert accepted.status_code == 303
    assert accepted.headers["location"] == f"/workspaces/{household_id}"
    assert shared_detail.status_code == 200
    assert "Owner Route" in shared_detail.text
    assert "Invitee Route" in shared_detail.text
    assert accepted_again.status_code == 400
    assert member_invite.status_code == 201


@pytest.mark.anyio
async def test_personal_workspace_rejects_invitation_route(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        await complete_sign_in(client)
        token = await csrf_token(client)
        with db_factory() as session:
            personal_id = session.scalar(select(Workspace.id).where(Workspace.is_personal))
            assert personal_id is not None
        response = await client.post(
            f"/workspaces/{personal_id}/invitations",
            data={"email": "invitee@example.test", "csrf_token": token},
        )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_expired_invitation_route_does_not_add_membership(
    db_factory: sessionmaker[Session],
) -> None:
    application = build_test_app(db_factory)

    async with (
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as owner_client,
        AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as invitee_client,
    ):
        await complete_sign_in(owner_client)
        owner_csrf = await csrf_token(owner_client)
        created = await owner_client.post(
            "/workspaces",
            data={"name": "Time Bound Home", "csrf_token": owner_csrf},
            follow_redirects=False,
        )
        household_id = int(created.headers["location"].rsplit("/", 1)[1])
        invited = await owner_client.post(
            f"/workspaces/{household_id}/invitations",
            data={"email": "late@example.test", "csrf_token": owner_csrf},
        )
        match = re.search(r"/invitations/([A-Za-z0-9_-]+)", invited.text)
        assert match is not None
        raw_token = match.group(1)

        with db_factory() as session:
            invitation = session.scalar(
                select(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == household_id)
            )
            assert invitation is not None
            invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
            session.commit()

        application.state.google_oauth.google.claims = verified_claims(
            sub="late-route-sub",
            email="late@example.test",
            name="Late Invitee",
        )
        await complete_sign_in(invitee_client)
        invitee_csrf = await csrf_token(invitee_client)
        response = await invitee_client.post(
            f"/invitations/{raw_token}/accept",
            data={"csrf_token": invitee_csrf},
            follow_redirects=False,
        )
        detail = await invitee_client.get(f"/workspaces/{household_id}")

    assert response.status_code == 400
    assert detail.status_code == 404
