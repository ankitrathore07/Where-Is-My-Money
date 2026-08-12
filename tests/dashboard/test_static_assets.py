from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Account, AccountBalanceSnapshot, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ScriptSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        source = dict(attrs).get("src")
        if source is not None:
            self.sources.append(source)


@pytest.mark.anyio
async def test_chartjs_is_pinned_local_and_licensed() -> None:
    """Removing a committed local dependency would break offline chart delivery."""
    application, _, engine = build_route_test_app(Path("test-static-assets"))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            chart, license_text, provenance = (
                await client.get("/static/vendor/chartjs/chart.umd.min.js"),
                await client.get("/static/vendor/chartjs/LICENSE.md"),
                await client.get("/static/vendor/chartjs/README.md"),
            )
    finally:
        engine.dispose()

    assert chart.status_code == 200
    assert chart.headers["content-type"].startswith("text/javascript")
    assert "Chart.js v4.5.1" in chart.text
    assert license_text.status_code == 200
    assert "MIT License" in license_text.text
    assert provenance.status_code == 200
    assert "4.5.1" in provenance.text


@pytest.mark.anyio
async def test_dashboard_assets_do_not_load_a_cdn(tmp_path: Path) -> None:
    """Changing dashboard scripts to a remote host would break offline use."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                account = Account(
                    workspace_id=workspace_id,
                    name="Checking",
                    account_type="checking",
                    is_liability=False,
                )
                session.add(account)
                session.flush()
                session.add(
                    AccountBalanceSnapshot(
                        workspace_id=workspace_id,
                        account_id=account.id,
                        balance_cents=100_000,
                        as_of_date=date(2026, 8, 10),
                        source="manual",
                    )
                )
                session.commit()

            response = await client.get(f"/workspaces/{workspace_id}/dashboard")
            assert response.status_code == 200
            parser = _ScriptSourceParser()
            parser.feed(response.text)
            sources = [urlparse(source) for source in parser.sources]
            assert "/static/vendor/chartjs/chart.umd.min.js" in [source.path for source in sources]
            assert "/static/dashboard.js" in [source.path for source in sources]
            assert all(source.netloc in {"", "testserver"} for source in sources)

            for source in sources:
                script = await client.get(source.geturl())
                assert script.status_code == 200
                assert script.headers["content-type"].startswith("text/javascript")
    finally:
        engine.dispose()
