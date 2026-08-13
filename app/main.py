"""FastAPI entry point for the web application."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.accounts.routes import router as account_router
from app.auth.dependencies import get_optional_current_user
from app.auth.oauth import build_google_oauth
from app.auth.routes import router as auth_router
from app.categories.routes import router as category_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging, logger
from app.core.middleware import (
    CSRFMiddleware,
    RequestContextMiddleware,
    UploadBodyLimitMiddleware,
    safe_request_path,
)
from app.core.security import SlidingWindowRateLimiter
from app.dashboard.routes import router as dashboard_router
from app.db.models import User
from app.documents.routes import router as document_router
from app.imports.routes import router as import_router
from app.imports.storage import LocalUploadStore
from app.payslips.extraction import DocumentExtractor, TesseractOcrEngine
from app.payslips.routes import router as payslip_router
from app.payslips.storage import PayslipUploadStore
from app.planning.routes import router as planning_router
from app.statement_imports.body_limit import StatementUploadBodyLimitMiddleware
from app.statement_imports.extraction import StatementDocumentExtractor
from app.statement_imports.routes import router as statement_import_router
from app.statement_imports.storage import StatementUploadStore
from app.transactions.routes import router as transaction_router
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
        logger.info("application.starting", extra={"app_env": configured.app_env})

        if configured.app_env.casefold() == "development":
            try:
                from alembic import command
                from alembic.config import Config

                alembic_cfg = Config(str(APP_DIRECTORY.parent / "alembic.ini"))
                alembic_cfg.set_main_option("sqlalchemy.url", configured.database_url)
                command.upgrade(alembic_cfg, "head")
                logger.info("migration.completed", extra={"state": "head"})
            except Exception as exc:  # pragma: no cover - environment-specific
                logger.warning(
                    "migration.failed",
                    extra={"error_code": "alembic_upgrade_failed"},
                    exc_info=exc,
                )

        from app.db.session import init_engine

        init_engine(configured.database_url)
        yield

        from app.db.session import dispose_engine

        dispose_engine()
        logger.info("application.stopped")

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
    application.state.upload_store = LocalUploadStore(
        configured.upload_directory,
        configured.max_csv_upload_bytes,
        configured.max_statement_upload_bytes,
    )
    application.state.payslip_store = PayslipUploadStore(
        configured.upload_directory, configured.max_payslip_upload_bytes
    )
    application.state.payslip_extractor = DocumentExtractor(TesseractOcrEngine())
    application.state.statement_store = StatementUploadStore(
        configured.upload_directory, configured.max_statement_upload_bytes
    )
    application.state.statement_extractor = StatementDocumentExtractor(
        DocumentExtractor(TesseractOcrEngine())
    )

    application.add_middleware(TrustedHostMiddleware, allowed_hosts=configured.trusted_hosts)

    application.add_middleware(
        SessionMiddleware,
        secret_key=configured.secret_key or "",
        session_cookie="wimm_session",
        max_age=7 * 24 * 60 * 60,
        same_site="lax",
        https_only=configured.session_https_only,
    )
    application.add_middleware(CSRFMiddleware, configured=configured)
    application.add_middleware(
        UploadBodyLimitMiddleware,
        max_file_bytes=max(
            configured.max_csv_upload_bytes,
            configured.max_payslip_upload_bytes,
            configured.max_statement_upload_bytes,
        ),
    )
    application.add_middleware(
        StatementUploadBodyLimitMiddleware,
        max_file_bytes=configured.max_statement_upload_bytes,
    )
    application.add_middleware(RequestContextMiddleware)
    application.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
    application.include_router(auth_router)
    application.include_router(workspace_router)
    application.include_router(dashboard_router)
    application.include_router(account_router)
    application.include_router(category_router)
    application.include_router(planning_router)
    application.include_router(document_router)
    application.include_router(import_router)
    application.include_router(payslip_router)
    application.include_router(statement_import_router)
    application.include_router(transaction_router)

    templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")

    def error_context(request: Request, status_code: int) -> dict[str, object]:
        return {
            "request": request,
            "current_user": None,
            "csrf_token": getattr(request.state, "csrf_token", ""),
            "status_code": status_code,
            "request_id": getattr(request.state, "request_id", "unavailable"),
        }

    def error_headers(request: Request, existing: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(existing or {})
        headers.update(
            {
                "X-Request-ID": getattr(request.state, "request_id", "unavailable"),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return headers

    def wants_html(request: Request) -> bool:
        return "text/html" in request.headers.get("accept", "").casefold()

    @application.exception_handler(StarletteHTTPException)
    async def http_error_page(request: Request, exc: StarletteHTTPException) -> Response:
        """Render a useful HTML page for browser-facing HTTP errors."""
        if exc.status_code < 400:
            return Response(status_code=exc.status_code, headers=exc.headers)
        if not wants_html(request):
            return JSONResponse(
                {"detail": exc.detail},
                status_code=exc.status_code,
                headers=exc.headers,
            )
        if exc.status_code == 404:
            return templates.TemplateResponse(
                request=request,
                name="errors/404.html",
                context=error_context(request, 404),
                status_code=404,
                headers=error_headers(request, exc.headers),
            )
        return templates.TemplateResponse(
            request=request,
            name="errors/error.html",
            context=error_context(request, exc.status_code),
            status_code=exc.status_code,
            headers=error_headers(request, exc.headers),
        )

    @application.exception_handler(Exception)
    async def unhandled_error_page(request: Request, exc: Exception) -> Response:
        """Log a correlation-safe event and hide exception details from the browser."""
        logger.error(
            "request.failed",
            extra={
                "request_id": getattr(request.state, "request_id", "unavailable"),
                "path": safe_request_path(request),
                "error_code": "unhandled_exception",
            },
            exc_info=exc,
        )
        if not wants_html(request):
            return JSONResponse(
                {
                    "detail": "Internal Server Error",
                    "request_id": getattr(request.state, "request_id", "unavailable"),
                },
                status_code=500,
                headers=error_headers(request),
            )
        return templates.TemplateResponse(
            request=request,
            name="errors/500.html",
            context=error_context(request, 500),
            status_code=500,
            headers=error_headers(request),
        )

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
