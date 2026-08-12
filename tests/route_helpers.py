import re
from collections.abc import Generator, Mapping
from pathlib import Path

from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.models import Base, Category
from app.db.session import get_db
from app.imports.storage import LocalUploadStore
from app.main import create_app
from app.payslips.storage import PayslipUploadStore


class FakeGoogleClient:
    def __init__(self, claims: Mapping[str, object]) -> None:
        self.claims = claims

    async def authorize_redirect(self, request, redirect_uri: str) -> RedirectResponse:
        request.session["_fake_oauth_state"] = "stored"
        return RedirectResponse("https://accounts.example.test/authorize", status_code=302)

    async def authorize_access_token(self, request) -> dict[str, object]:
        assert request.session.get("_fake_oauth_state") == "stored"
        return {"userinfo": dict(self.claims)}


class FakeOAuth:
    def __init__(self, claims: Mapping[str, object]) -> None:
        self.google = FakeGoogleClient(claims)


def verified_claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "sub": "import-route-sub",
        "email": "import-route@example.com",
        "email_verified": True,
        "name": "Import Route User",
    }
    claims.update(overrides)
    return claims


def build_route_test_app(tmp_path: Path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(Category(workspace_id=None, name="Uncategorized", kind="expense"))
        session.commit()
    configured = Settings(
        _env_file=None,
        app_env="development",
        secret_key="test-session-secret-that-is-long-enough",
        database_url="sqlite://",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        upload_directory=tmp_path,
    )
    application = create_app(configured, google_oauth=FakeOAuth(verified_claims()))
    application.state.upload_store = LocalUploadStore(tmp_path)
    application.state.payslip_store = PayslipUploadStore(tmp_path)

    def override_db() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_db
    return application, factory, engine


async def csrf_token(client: AsyncClient) -> str:
    response = await client.get("/")
    assert response.status_code == 200
    return client.cookies["wimm_csrf"]


async def complete_sign_in(client: AsyncClient) -> None:
    token = await csrf_token(client)
    started = await client.post("/auth/google", data={"csrf_token": token}, follow_redirects=False)
    assert started.status_code == 302
    callback = await client.get("/auth/google/callback", follow_redirects=False)
    assert callback.status_code == 303


def review_token(page: str, row_number: int) -> str:
    match = re.search(
        rf'name="review_token_{row_number}" value="([^"]+)"',
        page,
    )
    assert match is not None
    return match.group(1)
