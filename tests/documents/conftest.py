import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from sqlalchemy import select

from app.db.models import Category, Workspace
from app.payslips.extraction import ExtractedText
from tests.route_helpers import build_route_test_app

PDF_BYTES = b"%PDF-synthetic-browser"
PAY_TEXT = """Employer: Northstar Bicycle Works
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Net Pay: $3,700.00
Taxes: $900.00
Deductions: $400.00
"""


class BrowserFakeExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(PAY_TEXT, "embedded_text")


class BrowserFakeStatementExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(
            "08/01/2026 Example Market -$12.34 $1,250.00\n"
            "2026-08-02 Payroll CREDIT $2,500.00 $3,750.00",
            "embedded_text",
        )


@pytest.fixture(scope="module")
def browser(
    browser_name: str,
    browser_type_launch_args: dict[str, object],
) -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        launched = getattr(playwright, browser_name).launch(**browser_type_launch_args)
        yield launched
        launched.close()


@pytest.fixture
def context(browser: Browser) -> Generator[BrowserContext, None, None]:
    isolated_context = browser.new_context()
    yield isolated_context
    isolated_context.close()


@pytest.fixture
def page(context: BrowserContext) -> Generator[Page, None, None]:
    browser_page = context.new_page()
    yield browser_page
    browser_page.close()


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


@pytest.fixture
def live_document_app(tmp_path: Path) -> Generator[tuple[str, object], None, None]:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.payslip_extractor = BrowserFakeExtractor()
    application.state.statement_extractor = BrowserFakeStatementExtractor()
    with factory() as session:
        session.add(Category(workspace_id=None, name="Income", kind="income"))
        session.commit()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", factory
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        engine.dispose()


@pytest.fixture
def signed_in_upload_page(
    page: Page,
    context: BrowserContext,
    live_document_app: tuple[str, object],
) -> tuple[Page, int]:
    base_url, factory = live_document_app
    page.goto(base_url)
    csrf = page.locator('input[name="csrf_token"]').first.get_attribute("value")
    assert csrf
    started = context.request.post(
        f"{base_url}/auth/google",
        form={"csrf_token": csrf},
        max_redirects=0,
    )
    assert started.status == 302
    callback = context.request.get(f"{base_url}/auth/google/callback", max_redirects=0)
    assert callback.status == 303
    with factory() as session:
        workspace_id = session.scalar(select(Workspace.id))
        assert workspace_id is not None
    page.goto(f"{base_url}/workspaces/{workspace_id}/documents/new")
    return page, workspace_id
