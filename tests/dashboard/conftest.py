import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from tests.route_helpers import build_route_test_app


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
def live_dashboard_app(tmp_path: Path) -> Generator[tuple[str, object], None, None]:
    application, factory, engine = build_route_test_app(tmp_path)
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
