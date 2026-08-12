import re

from playwright.sync_api import Browser, BrowserContext, Page, expect
from sqlalchemy import select

from app.categorization.builtins import BUILTIN_CATEGORY_DEFINITIONS
from app.dashboard.demo import seed_dashboard_demo
from app.db.models import Category, User


def _sign_in_and_seed_demo(
    page: Page,
    context: BrowserContext,
    live_dashboard_app: tuple[str, object],
) -> tuple[str, int]:
    base_url, factory = live_dashboard_app
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
        session.add_all(
            Category(workspace_id=None, name=name, kind=kind)
            for name, kind in BUILTIN_CATEGORY_DEFINITIONS
            if name != "Uncategorized"
        )
        user = session.scalar(select(User).where(User.email == "import-route@example.com"))
        assert user is not None
        workspace = seed_dashboard_demo(session, user)
        workspace_id = workspace.id
        session.commit()
    return base_url, workspace_id


def test_demo_spending_dashboard_enhances_charts_changes_period_and_drills_down(
    page: Page,
    context: BrowserContext,
    live_dashboard_app: tuple[str, object],
) -> None:
    browser_errors: list[str] = []
    page.on("pageerror", lambda error: browser_errors.append(str(error)))
    base_url, workspace_id = _sign_in_and_seed_demo(page, context, live_dashboard_app)

    page.goto(f"{base_url}/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")

    expect(page.get_by_role("heading", name="Where am I spending?")).to_be_visible()
    expect(page.get_by_text("$2,328.00", exact=True).first).to_be_visible()
    expect(page.get_by_text("Fictional Apartments", exact=True).first).to_be_attached()
    expect(page.get_by_text("Neighborhood Market", exact=True).first).to_be_attached()
    expect(page.locator("#spending-category-chart")).to_be_visible()
    expect(page.locator("#spending-merchant-chart")).to_be_visible()
    expect(page.locator("[data-dashboard-root]")).to_have_class(
        re.compile(r"spending-category-chart-ready")
    )
    expect(page.locator("[data-dashboard-root]")).to_have_class(
        re.compile(r"spending-merchant-chart-ready")
    )
    expect(page.locator("table caption", has_text="Spending by category")).to_have_count(1)
    expect(page.locator("table caption", has_text="Spending by merchant")).to_have_count(1)

    page.get_by_label("Period").select_option("last_5_years")
    page.get_by_role("button", name="Update spending").click()

    expect(page).to_have_url(re.compile(r"spending_period=last_5_years"))
    expect(page.get_by_text("$263,328.00", exact=True).first).to_be_visible()
    page.get_by_role("link", name="View all supporting transactions").click()

    expect(page).to_have_url(re.compile(r"spending=only"))
    expect(
        page.get_by_text("Showing categorized spending supporting the dashboard report.")
    ).to_be_visible()
    expect(page.locator("tbody tr")).to_have_count(10)
    assert browser_errors == []


def test_demo_spending_tables_remain_visible_without_javascript(
    browser: Browser,
    live_dashboard_app: tuple[str, object],
) -> None:
    with browser.new_context(java_script_enabled=False) as context:
        page = context.new_page()
        base_url, workspace_id = _sign_in_and_seed_demo(page, context, live_dashboard_app)

        page.goto(f"{base_url}/workspaces/{workspace_id}/dashboard?as_of=2026-08-10")

        expect(page.get_by_role("heading", name="Where am I spending?")).to_be_visible()
        expect(page.locator(".dashboard-category-fallback")).to_be_visible()
        expect(page.locator(".dashboard-merchant-fallback")).to_be_visible()
        expect(page.locator("[data-dashboard-root]")).not_to_have_class(
            re.compile(r"spending-category-chart-ready|spending-merchant-chart-ready")
        )
        expect(
            page.get_by_role("row", name=re.compile(r"Housing 1 \$1,800\.00 77\.3%"))
        ).to_be_visible()
        expect(
            page.get_by_role("row", name=re.compile(r"Neighborhood Market 2 \$335\.00 14\.4%"))
        ).to_be_visible()

        page.close()
