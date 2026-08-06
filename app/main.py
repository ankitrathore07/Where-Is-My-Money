"""FastAPI entry point for the web application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.logging import configure_logging, logger

APP_DIRECTORY = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Configure logging, apply migrations (dev), and manage the DB engine.

    In development we run ``alembic upgrade head`` on startup for convenience so
    a fresh checkout just works. In production, migrations are applied out of
    band (CI or a deploy script) — never via the web process startup.
    """
    configure_logging()
    logger.info("Starting Where Is My Money (%s)", settings.app_env)

    if settings.app_env.lower() == "development":
        try:
            from alembic import command
            from alembic.config import Config

            alembic_cfg = Config(str(APP_DIRECTORY.parent / "alembic.ini"))
            alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
            command.upgrade(alembic_cfg, "head")
            logger.info("Applied Alembic migrations to head")
        except Exception as exc:  # pragma: no cover - environment-specific
            logger.warning("Could not apply Alembic migrations: %s", exc)

    from app.db.session import init_engine

    init_engine(settings.database_url)

    yield

    from app.db.session import dispose_engine

    dispose_engine()
    logger.info("Shutting down application")


app = FastAPI(
    title="Where Is My Money",
    description="A privacy-conscious personal finance learning project.",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Render the first server-side HTML page."""
    return templates.TemplateResponse(request=request, name="home.html")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a tiny response used by tests and deployment health checks."""
    return {"status": "ok"}
