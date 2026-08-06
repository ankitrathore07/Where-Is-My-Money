"""FastAPI entry point for the web application."""

from pathlib import Path
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.logging import configure_logging, logger
from app.core.config import settings

APP_DIRECTORY = Path(__file__).resolve().parent

app = FastAPI(
    title="Where Is My Money",
    description="A privacy-conscious personal finance learning project.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")


@app.on_event("startup")
async def startup_event() -> None:
    """Configure logging and create DB tables in development for convenience.

    Prefer running Alembic migrations in CI/production. The metadata.create_all
    path here is a harmless convenience for local development when Alembic is
    not available; it intentionally catches import errors so the app still
    starts when dependencies like SQLAlchemy are not installed.
    """
    configure_logging()
    logger.info("Starting Where Is My Money (%s)", settings.app_env)

    if settings.app_env and settings.app_env.lower() == "development":
        try:
            # Initialize the DB engine & session factory and create tables for dev.
            from app.db.models import Base  # type: ignore
            from app.db.session import init_engine, get_engine  # type: ignore

            engine = init_engine(settings.database_url)
            logger.info("Creating DB tables from metadata for development")
            Base.metadata.create_all(engine)
        except Exception as exc:  # pragma: no cover - environment-specific
            logger.info("Skipping metadata.create_all: %s", exc)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    # Dispose of the engine (if initialized) to close pooled connections.
    try:
        from app.db.session import dispose_engine  # type: ignore

        dispose_engine()
    except Exception:
        pass

    logger.info("Shutting down application")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Render the first server-side HTML page."""
    return templates.TemplateResponse(request=request, name="home.html")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a tiny response used by tests and deployment health checks."""
    return {"status": "ok"}
