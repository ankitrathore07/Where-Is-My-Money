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
        self.navigation_attributes: dict[str, str] = {}
        self.elements: list[dict[str, str]] = []
        self.forms: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "nav" and attributes.get("class") == "site-nav":
            self._inside_navigation = True
            self.navigation_attributes = attributes
        elif self._inside_navigation and tag == "form":
            self.forms.append(attributes)
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_workspace_page_exposes_all_authenticated_navigation_destinations(
    tmp_path: Path,
) -> None:
    """Removing a shared destination would make it unreachable from Income."""
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
    assert navigation.navigation_attributes["aria-label"] == "Account navigation"
    assert "hidden" not in navigation.navigation_attributes
    assert "inert" not in navigation.navigation_attributes
    assert navigation.navigation_attributes.get("aria-hidden") != "true"
    assert [(element["text"], element.get("href")) for element in navigation.elements] == [
        ("Workspaces", "/workspaces"),
        ("Dashboard", f"/workspaces/{workspace_id}/dashboard"),
        ("Accounts", f"/workspaces/{workspace_id}/accounts"),
        ("Planning", f"/workspaces/{workspace_id}/planning"),
        ("Transactions", f"/workspaces/{workspace_id}/transactions"),
        ("Income", f"/workspaces/{workspace_id}/income"),
        ("Categories", f"/workspaces/{workspace_id}/categories"),
        ("Tags", f"/workspaces/{workspace_id}/tags"),
        ("Sign out", None),
    ]
    assert navigation.forms == [{"action": "/auth/sign-out", "method": "post"}]
    for element in navigation.elements:
        assert "hidden" not in element
        assert "inert" not in element
        assert element.get("aria-hidden") != "true"
