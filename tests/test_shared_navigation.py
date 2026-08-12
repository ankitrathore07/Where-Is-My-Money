"""Regression coverage for the shared authenticated navigation."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


class _NavigationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._inside_navigation = False
        self._active_element: dict[str, str] | None = None
        self.elements: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "nav" and attributes.get("class") == "site-nav":
            self._inside_navigation = True
        if self._inside_navigation and tag in {"a", "button"}:
            self._active_element = {"tag": tag, **attributes, "text": ""}

    def handle_data(self, data: str) -> None:
        if self._active_element is not None:
            self._active_element["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if self._active_element is not None and tag == self._active_element["tag"]:
            self._active_element["text"] = self._active_element["text"].strip()
            self.elements.append(self._active_element)
            self._active_element = None
        if tag == "nav":
            self._inside_navigation = False


def _mobile_stylesheet(css: str) -> str:
    media_start = css.index("@media (max-width: 42.5rem)")
    block_start = css.index("{", media_start)
    depth = 0
    for index, character in enumerate(css[block_start:], start=block_start):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return css[block_start + 1 : index]
    raise AssertionError("The mobile navigation media query is not closed")


def _mobile_rule_hides(element: dict[str, str], css: str) -> bool:
    """Evaluate the relevant simple selectors against the rendered nav element."""
    for rule in _mobile_stylesheet(css).split("}"):
        if "{" not in rule:
            continue
        selectors, declarations = rule.split("{", maxsplit=1)
        if "display: none" not in declarations:
            continue
        for selector in selectors.split(","):
            normalized = " ".join(selector.split())
            if normalized == ".site-nav a" and element["tag"] == "a":
                return True
            if (
                normalized == '.site-nav a:not([href*="/dashboard"])'
                and element["tag"] == "a"
                and "/dashboard" not in element["href"]
            ):
                return True
            if normalized == ".site-nav .link-button" and "link-button" in element.get(
                "class", ""
            ).split():
                return True
    return False


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_mobile_workspace_navigation_keeps_all_authenticated_destinations_visible(
    tmp_path: Path,
) -> None:
    """A mobile-only hide rule would make Income and sign-out unreachable."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.get(f"/workspaces/{workspace_id}/income")
    finally:
        engine.dispose()

    assert response.status_code == 200
    navigation = _NavigationParser()
    navigation.feed(response.text)
    assert [(element["text"], element.get("href")) for element in navigation.elements] == [
        ("Workspaces", "/workspaces"),
        ("Dashboard", f"/workspaces/{workspace_id}/dashboard"),
        ("Accounts", f"/workspaces/{workspace_id}/accounts"),
        ("Transactions", f"/workspaces/{workspace_id}/transactions"),
        ("Income", f"/workspaces/{workspace_id}/income"),
        ("Categories", f"/workspaces/{workspace_id}/categories"),
        ("Sign out", None),
    ]

    css = (Path(__file__).parents[1] / "app" / "static" / "styles.css").read_text()
    hidden = [
        element["text"] for element in navigation.elements if _mobile_rule_hides(element, css)
    ]
    assert hidden == []
