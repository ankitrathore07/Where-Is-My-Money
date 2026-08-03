"""FastAPI entry point for the web application."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIRECTORY = Path(__file__).resolve().parent

app = FastAPI(
    title="Where Is My Money",
    description="A privacy-conscious personal finance learning project.",
    version="0.1.0",
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
