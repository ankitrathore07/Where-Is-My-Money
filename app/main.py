"""FastAPI entry point for the web application."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.auth.dependencies import get_optional_current_user
from app.auth.oauth import build_google_oauth
from app.auth.routes import router as auth_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging, logger
from app.core.middleware import CSRFMiddleware
from app.core.security import SlidingWindowRateLimiter
from app.db.models import User
from app.workspaces.routes import router as workspace_router

APP_DIRECTORY = Path(__file__).resolve().parent


def create_app(
    app_settings: Settings | None = None,
    *,
    google_oauth: object | None = None,
) -> FastAPI:
    """Assemble an application with injectable settings and OAuth for tests."""
    configured = app_settings or settings

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Apply development migrations and manage the database engine."""
        configure_logging()
        logger.info("Starting Where Is My Money (%s)", configured.app_env)

        if configured.app_env.casefold() == "development":
            try:
                from alembic import command
                from alembic.config import Config

                alembic_cfg = Config(str(APP_DIRECTORY.parent / "alembic.ini"))
                alembic_cfg.set_main_option("sqlalchemy.url", configured.database_url)
                command.upgrade(alembic_cfg, "head")
                logger.info("Applied Alembic migrations to head")
            except Exception as exc:  # pragma: no cover - environment-specific
                logger.warning("Could not apply Alembic migrations: %s", exc)

        from app.db.session import init_engine

        init_engine(configured.database_url)
        yield

        from app.db.session import dispose_engine

        dispose_engine()
        logger.info("Shutting down application")

    application = FastAPI(
        title="Where Is My Money",
        description="A privacy-conscious personal finance learning project.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.state.google_oauth = google_oauth or _configured_google_oauth(configured)
    application.state.auth_rate_limiter = SlidingWindowRateLimiter(
        limit=10,
        window_seconds=60,
    )

    application.add_middleware(
        SessionMiddleware,
        secret_key=configured.secret_key or "",
        session_cookie="wimm_session",
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=configured.session_https_only,
    )
    application.add_middleware(CSRFMiddleware, configured=configured)
    application.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
    application.include_router(auth_router)
    application.include_router(workspace_router)

    templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")

    @application.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        current_user: Annotated[User | None, Depends(get_optional_current_user)],
    ) -> HTMLResponse:
        """Render the home page with optional signed-session identity."""
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "current_user": current_user,
                "csrf_token": request.state.csrf_token,
            },
        )

    @application.get("/health")
    async def health_check() -> dict[str, str]:
        """Return a tiny response used by tests and deployment health checks."""
        return {"status": "ok"}

    return application


def _configured_google_oauth(configured: Settings):
    if not configured.google_client_id or not configured.google_client_secret:
        return None
    return build_google_oauth(configured)


app = create_app()
