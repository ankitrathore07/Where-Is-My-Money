import re
import socket
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright
from sqlalchemy import select

from app.db.models import Category, Workspace
from tests.route_helpers import build_route_test_app


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


def _run_browser_scenario(
    scenario: Callable[[Page, BrowserContext], None],
    *,
    java_script_enabled: bool = True,
    viewport: dict[str, int] | None = None,
) -> None:
    """Keep Playwright's sync event loop isolated from AnyIO's pytest runner."""
    failures: list[BaseException] = []

    def run() -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(
                    java_script_enabled=java_script_enabled,
                    viewport=viewport,
                )
                page = context.new_page()
                try:
                    scenario(page, context)
                finally:
                    context.close()
                    browser.close()
        except BaseException as exc:  # pragma: no cover - re-raised on the test thread
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "Browser scenario exceeded 30 seconds."
    if failures:
        raise failures[0]


@pytest.fixture
def live_rules_app(tmp_path: Path) -> Generator[tuple[str, object], None, None]:
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


def _sign_in_and_seed(page: Page, context: BrowserContext, live_rules_app) -> tuple[str, int]:
    base_url, factory = live_rules_app
    page.goto(base_url)
    csrf = page.locator('input[name="csrf_token"]').first.get_attribute("value")
    assert csrf
    assert (
        context.request.post(
            f"{base_url}/auth/google", form={"csrf_token": csrf}, max_redirects=0
        ).status
        == 302
    )
    assert context.request.get(f"{base_url}/auth/google/callback", max_redirects=0).status == 303
    with factory() as session:
        workspace_id = session.scalar(select(Workspace.id))
        assert workspace_id is not None
        session.add(
            Category(
                workspace_id=workspace_id,
                name="Coffee",
                name_key="coffee",
                kind="expense",
            )
        )
        session.commit()
    return base_url, workspace_id


def _fill_minimum_rule(page: Page) -> None:
    page.get_by_label("Rule name").fill("Keyboard coffee")
    page.get_by_label("Condition field 1").select_option("description")
    page.get_by_label("Condition operator 1").select_option("contains")
    page.get_by_label("Text value 1").fill("COFFEE")
    page.get_by_label("Category").select_option(label="Coffee")


def test_keyboard_create_preview_confirmation_and_row_enhancement(
    live_rules_app: tuple[str, object],
) -> None:
    """Break if the builder loses keyboard focus, preview, or explicit confirmation."""

    def scenario(page: Page, context: BrowserContext) -> None:
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        base_url, workspace_id = _sign_in_and_seed(page, context, live_rules_app)
        page.goto(f"{base_url}/workspaces/{workspace_id}/rules/new")

        _fill_minimum_rule(page)
        add = page.get_by_role("button", name="Add condition")
        add.focus()
        add.press("Enter")
        expect(page.locator("[data-condition-row]")).to_have_count(2)
        expect(page.get_by_label("Condition field 2")).to_be_focused()
        page.get_by_label("Condition field 2").select_option("direction")
        page.get_by_label("Direction value 2").select_option("expense")
        page.get_by_role("button", name="Preview rule").click()

        expect(page.get_by_role("heading", name="Review rule impact")).to_be_visible()
        page.get_by_role("button", name="Confirm and save rule").click()
        expect(page).to_have_url(re.compile(r"/rules$"))
        expect(page.get_by_text("Keyboard coffee", exact=True)).to_be_visible()
        assert errors == []

    _run_browser_scenario(scenario)


def test_no_javascript_rule_creation_remains_complete(live_rules_app: tuple[str, object]) -> None:
    """Break if preview and confirmation depend on client-side JavaScript."""

    def scenario(page: Page, context: BrowserContext) -> None:
        base_url, workspace_id = _sign_in_and_seed(page, context, live_rules_app)
        page.goto(f"{base_url}/workspaces/{workspace_id}/rules/new")

        page.get_by_role("button", name="Add condition").click()
        expect(page.locator("[data-condition-row]")).to_have_count(2)
        _fill_minimum_rule(page)
        page.locator('input[name="condition_remove_1"]').check()
        page.get_by_role("button", name="Preview rule").click()
        expect(page.get_by_role("heading", name="Review rule impact")).to_be_visible()
        page.get_by_role("button", name="Confirm and save rule").click()
        expect(page.get_by_text("Keyboard coffee", exact=True)).to_be_visible()

    _run_browser_scenario(scenario, java_script_enabled=False)


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 800}, {"width": 390, "height": 844}]
)
def test_rules_pages_do_not_overflow_supported_viewports(
    live_rules_app: tuple[str, object],
    viewport: dict[str, int],
) -> None:
    """Break if the builder forces horizontal page scrolling on desktop or mobile."""

    def scenario(page: Page, context: BrowserContext) -> None:
        base_url, workspace_id = _sign_in_and_seed(page, context, live_rules_app)
        page.goto(f"{base_url}/workspaces/{workspace_id}/rules/new")
        page.get_by_role("button", name="Add condition").click()

        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        overflowing = page.locator("body *").evaluate_all(
            """(elements, width) => elements
              .map((element) => {
                const rect = element.getBoundingClientRect();
                return {tag: element.tagName, left: rect.left, right: rect.right};
              })
              .filter((element) => element.left < 0 || element.right > width)""",
            client_width,
        )
        overflowing_rule_checks = page.locator(".rule-page .compact-check").evaluate_all(
            """(elements) => elements
              .filter((element) => element.scrollWidth > element.clientWidth)
              .map((element) => ({
                text: element.textContent.trim(),
                clientWidth: element.clientWidth,
                scrollWidth: element.scrollWidth,
              }))"""
        )
        assert scroll_width == client_width, overflowing
        assert overflowing_rule_checks == []

    _run_browser_scenario(scenario, viewport=viewport)
