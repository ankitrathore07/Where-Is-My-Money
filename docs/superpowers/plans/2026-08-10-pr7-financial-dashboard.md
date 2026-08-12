# PR 7 Centralized Financial Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-scoped account setup, manual balances, and a polished centralized dashboard with deterministic current-position and five-year financial trends.

**Architecture:** Keep writes in a focused account service and reads in a read-only dashboard service. Both accept the authorized workspace ID explicitly and return immutable data types using integer cents and calendar dates. Thin FastAPI/Jinja routes render numeric HTML first, then locally vendored Chart.js progressively enhances the two trend sections.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Jinja2, SQLite, Chart.js 4.5.1 UMD served locally, plain JavaScript, CSS, Pytest, Ruff, Alembic.

## Global Constraints

- Work on `codex/pr-7-financial-dashboard`, based on merged PR 6, without coupling dashboard calculations to payslips or income records.
- Do not add LangGraph, an LLM, prompts, analytics, telemetry, or a browser network dependency.
- Store and calculate money as integer cents. Use `Decimal` only while validating a member's dollar input; never use binary floating point.
- Every account, balance, transaction, aggregate, chart payload, route, validation response, and demo workspace must remain workspace-scoped.
- Normal manual balances are non-negative. `Account.is_liability` determines subtraction; existing exceptional negative snapshots remain valid inputs to reports.
- The default report cutoff is the latest transaction or balance-snapshot date in the authorized workspace. Tests pass explicit dates.
- Transfer categories do not count as income or spending. Sign/category mismatches and missing categories count as “needs review” instead of being guessed.
- No migration is planned; the existing `accounts` and `account_balance_snapshots` schema is sufficient.
- Chart.js must be pinned to 4.5.1, stored under `app/static/vendor/chartjs/`, accompanied by its MIT license and a provenance README, and loaded without a CDN.
- Numeric dashboard facts and fallback trend tables must remain readable when JavaScript or Chart.js fails.
- The layout must have no horizontal overflow at 360 CSS pixels and must respect `prefers-reduced-motion`.
- Use only synthetic account, balance, institution, transaction, and user data in tests and demo fixtures.
- Each task follows red-green-refactor: add a focused failing test, run it and observe the expected failure, implement the minimum behavior, rerun focused tests, then commit.

## File and responsibility map

- `app/accounts/types.py`: account-type catalog and immutable form commands.
- `app/accounts/service.py`: validation, scoped account mutations, balance mutations, and stable account listing.
- `app/accounts/routes.py`: authorized HTML routes and form error mapping.
- `app/dashboard/types.py`: immutable current-position, cash-flow, trend, highlight, and complete-report results.
- `app/dashboard/service.py`: all workspace-scoped report queries and deterministic calculations; no commits.
- `app/dashboard/presentation.py`: dollar/percent formatting and bounded Chart.js payload construction.
- `app/dashboard/routes.py`: authorized dashboard route and strict optional as-of query parsing.
- `app/dashboard/demo.py`: explicit synthetic demo workspace seeding for an existing signed-in user.
- `app/templates/accounts/`: account list, create/edit form, and manual balance form.
- `app/templates/dashboard/index.html`: approved hybrid dashboard, semantic numeric facts, and fallback tables.
- `app/static/dashboard.js`: local chart initialization and progressive-enhancement state.
- `app/static/vendor/chartjs/`: pinned Chart.js UMD file, license, and provenance.
- `app/static/styles.css`: shared account and responsive dashboard visual system.
- `tests/accounts/`: account service and account route tests.
- `tests/dashboard/`: report service, route, presentation, demo, and acceptance tests.
- `app/dashboard/demo_data.json`: packaged fictional five-year demo inputs and exact expected totals used by both the CLI and tests.

---

### Task 1: Validate and mutate workspace accounts

**Files:**
- Create: `app/accounts/__init__.py`
- Create: `app/accounts/types.py`
- Create: `app/accounts/service.py`
- Create: `tests/accounts/__init__.py`
- Create: `tests/accounts/test_service.py`

**Interfaces:**
- Consumes: existing `Account`, `AccountBalanceSnapshot`, and SQLAlchemy `Session`.
- Produces: `ACCOUNT_TYPE_OPTIONS`, `AccountInput`, `ManualBalanceInput`, `AccountValidationError`, `AccountNotFoundError`, `create_account()`, `update_account()`, `get_workspace_account()`, `list_workspace_accounts()`, and `add_manual_balance()`.

- [ ] **Step 1: Add failing catalog and account validation tests**

Create `tests/accounts/test_service.py` with these imports and core assertions:

```python
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.accounts.service import (
    AccountNotFoundError,
    AccountValidationError,
    add_manual_balance,
    create_account,
    get_workspace_account,
    list_workspace_accounts,
    update_account,
)
from app.accounts.types import ACCOUNT_TYPE_OPTIONS, AccountInput, ManualBalanceInput
from app.db.models import Account, AccountBalanceSnapshot, Workspace


def test_account_type_catalog_uses_schema_values_and_fixed_classifications() -> None:
    by_value = {option.value: option for option in ACCOUNT_TYPE_OPTIONS}
    assert tuple(by_value) == (
        "checking",
        "savings",
        "credit_card",
        "investment_401k",
        "investment_brokerage",
        "mortgage",
        "auto_loan",
        "student_loan",
        "other",
    )
    assert by_value["checking"].default_is_liability is False
    assert by_value["mortgage"].default_is_liability is True
    assert by_value["other"].default_is_liability is None


def test_create_account_normalizes_text_and_persists_integer_workspace_id(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(
        session,
        workspace.id,
        AccountInput("  Everyday   Checking  ", "checking", " Example  CU ", False),
    )
    session.commit()
    assert account.name == "Everyday Checking"
    assert account.institution == "Example CU"
    assert account.workspace_id == workspace.id
    assert account.is_liability is False


@pytest.mark.parametrize(
    ("values", "field"),
    [
        (AccountInput("   ", "checking", "", False), "name"),
        (AccountInput("x" * 256, "checking", "", False), "name"),
        (AccountInput("Card", "unknown", "", True), "account_type"),
        (AccountInput("Checking", "checking", "", True), "is_liability"),
        (AccountInput("Mortgage", "mortgage", "", False), "is_liability"),
        (AccountInput("Other", "other", "x" * 256, False), "institution"),
    ],
)
def test_invalid_account_input_reports_the_specific_field(
    session: Session, workspace: Workspace, values: AccountInput, field: str
) -> None:
    with pytest.raises(AccountValidationError) as error:
        create_account(session, workspace.id, values)
    assert field in error.value.field_errors
    assert session.query(Account).count() == 0
```

- [ ] **Step 2: Run the account service test and confirm the missing-module failure**

Run:

```powershell
uv run pytest tests/accounts/test_service.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.accounts'`.

- [ ] **Step 3: Define the immutable commands and account-type catalog**

Create `app/accounts/types.py` with frozen dataclasses:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class AccountTypeOption:
    value: str
    label: str
    default_is_liability: bool | None


ACCOUNT_TYPE_OPTIONS = (
    AccountTypeOption("checking", "Checking", False),
    AccountTypeOption("savings", "Savings", False),
    AccountTypeOption("credit_card", "Credit card", True),
    AccountTypeOption("investment_401k", "401(k)", False),
    AccountTypeOption("investment_brokerage", "Brokerage", False),
    AccountTypeOption("mortgage", "Mortgage", True),
    AccountTypeOption("auto_loan", "Auto loan", True),
    AccountTypeOption("student_loan", "Student loan", True),
    AccountTypeOption("other", "Other", None),
)


@dataclass(frozen=True)
class AccountInput:
    name: str
    account_type: str
    institution: str
    is_liability: bool


@dataclass(frozen=True)
class ManualBalanceInput:
    amount: str
    as_of_date: str
```

Create empty package files `app/accounts/__init__.py` and `tests/accounts/__init__.py`.

- [ ] **Step 4: Implement minimal account validation and stable scoped listing**

In `app/accounts/service.py`, define the errors and bound:

```python
MAX_BALANCE_CENTS = 9_000_000_000_000_000


class AccountValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the account details below.")
        self.field_errors = field_errors


class AccountNotFoundError(LookupError):
    pass
```

Implement these exact signatures: `create_account(session: Session,
workspace_id: int, values: AccountInput) -> Account`;
`update_account(session: Session, workspace_id: int, account_id: int, values:
AccountInput) -> Account`; `get_workspace_account(session: Session,
workspace_id: int, account_id: int) -> Account`;
`list_workspace_accounts(session: Session, workspace_id: int) ->
tuple[Account, ...]`; and `add_manual_balance(session: Session, workspace_id:
int, account_id: int, values: ManualBalanceInput, *, today: date) ->
AccountBalanceSnapshot`.

Normalize name/institution with `" ".join(value.split())`. Validate all fields before adding an ORM object. For a catalog option whose `default_is_liability` is not `None`, reject a mismatching posted classification. Allow either classification for `other`. `list_workspace_accounts()` must filter `Account.workspace_id == workspace_id` and order by `account_type`, `func.lower(Account.name)`, then `Account.id`.

- [ ] **Step 5: Add failing update, isolation, ordering, and balance-boundary tests**

Append tests that prove:

```python
def test_update_and_get_never_cross_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    foreign = Account(
        workspace_id=other_workspace.id,
        name="SECRET Brokerage",
        account_type="investment_brokerage",
        is_liability=False,
    )
    session.add(foreign)
    session.commit()
    with pytest.raises(AccountNotFoundError):
        get_workspace_account(session, workspace.id, foreign.id)
    with pytest.raises(AccountNotFoundError):
        update_account(
            session,
            workspace.id,
            foreign.id,
            AccountInput("Changed", "investment_brokerage", "", False),
        )
    assert foreign.name == "SECRET Brokerage"


@pytest.mark.parametrize(
    ("amount", "field"),
    [
        ("", "amount"),
        ("-1.00", "amount"),
        ("1.001", "amount"),
        ("not-money", "amount"),
        ("90000000000000.01", "amount"),
    ],
)
def test_manual_balance_rejects_invalid_amounts(
    session: Session, workspace: Workspace, amount: str, field: str
) -> None:
    account = create_account(session, workspace.id, AccountInput("Savings", "savings", "", False))
    with pytest.raises(AccountValidationError) as error:
        add_manual_balance(
            session,
            workspace.id,
            account.id,
            ManualBalanceInput(amount, "2026-08-10"),
            today=date(2026, 8, 10),
        )
    assert field in error.value.field_errors


def test_manual_balance_converts_exact_cents_and_rejects_future_date(
    session: Session, workspace: Workspace
) -> None:
    account = create_account(session, workspace.id, AccountInput("Mortgage", "mortgage", "", True))
    snapshot = add_manual_balance(
        session,
        workspace.id,
        account.id,
        ManualBalanceInput("83130.45", "2026-08-10"),
        today=date(2026, 8, 10),
    )
    assert snapshot.balance_cents == 8_313_045
    assert snapshot.workspace_id == workspace.id
    assert snapshot.source == "manual"
    with pytest.raises(AccountValidationError) as error:
        add_manual_balance(
            session,
            workspace.id,
            account.id,
            ManualBalanceInput("1.00", "2026-08-11"),
            today=date(2026, 8, 10),
        )
    assert "as_of_date" in error.value.field_errors
```

Also assert listing excludes `other_workspace`, and case-insensitive names use ID as the final tie-breaker.

- [ ] **Step 6: Implement exact Decimal/date parsing and update behavior**

Parse dollars with `Decimal`, reject exponent notation, NaN/infinity, negative values, more than two decimal places, and values above `MAX_BALANCE_CENTS`. Parse only `YYYY-MM-DD` with `date.fromisoformat`, compare against injected `today`, and copy the already-scoped account's `workspace_id` into the snapshot. Call `session.flush()` but never `session.commit()` in service functions.

- [ ] **Step 7: Run focused tests, Ruff, and commit**

Run:

```powershell
uv run pytest tests/accounts/test_service.py -v
uv run ruff check app/accounts tests/accounts
uv run ruff format --check app/accounts tests/accounts
```

Expected: all account service tests and both Ruff commands pass.

Commit:

```powershell
git add app/accounts tests/accounts
git commit -m "feat: manage workspace accounts and balances"
```

---

### Task 2: Add authorized account and balance screens

**Files:**
- Create: `app/accounts/routes.py`
- Create: `app/templates/accounts/index.html`
- Create: `app/templates/accounts/form.html`
- Create: `app/templates/accounts/balance_form.html`
- Create: `tests/accounts/test_routes.py`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/templates/workspace_detail.html`

**Interfaces:**
- Consumes: Task 1 commands, services, exceptions, `require_workspace`, `require_csrf`, and route-test helpers.
- Produces: account list/create/edit/manual-balance routes beneath `/workspaces/{workspace_id}`.

- [ ] **Step 1: Write failing authentication and happy-path route tests**

Create `tests/accounts/test_routes.py` using `build_route_test_app()` and `complete_sign_in()`:

```python
@pytest.mark.anyio
async def test_accounts_require_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/accounts", follow_redirects=False)
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_member_can_create_edit_and_add_manual_balance(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                workspace_id = workspace.id
            token = client.cookies["wimm_csrf"]
            created = await client.post(
                f"/workspaces/{workspace_id}/accounts",
                data={
                    "csrf_token": token,
                    "name": "Everyday Checking",
                    "account_type": "checking",
                    "institution": "Example CU",
                    "classification": "asset",
                },
                follow_redirects=False,
            )
            assert created.status_code == 303
            with factory() as session:
                account = session.scalar(select(Account))
                assert account is not None
                account_id = account.id
            balance = await client.post(
                f"/workspaces/{workspace_id}/accounts/{account_id}/balances",
                data={
                    "csrf_token": token,
                    "amount": "8420.50",
                    "as_of_date": "2026-08-10",
                },
                follow_redirects=False,
            )
            assert balance.status_code == 303
            with factory() as session:
                saved = session.scalar(select(AccountBalanceSnapshot))
                assert saved is not None
                assert saved.balance_cents == 842_050
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run route tests and confirm the missing-router failure**

Run `uv run pytest tests/accounts/test_routes.py -v`.

Expected: `/workspaces/1/accounts` returns 404 after sign-in or route imports fail because `app.accounts.routes` does not exist.

- [ ] **Step 3: Implement thin account routes and register the router**

Create `APIRouter(prefix="/workspaces/{workspace_id}", tags=["accounts"])` with these endpoints:

```text
GET  /accounts
GET  /accounts/new
POST /accounts
GET  /accounts/{account_id}/edit
POST /accounts/{account_id}
GET  /accounts/{account_id}/balances/new
POST /accounts/{account_id}/balances
```

All endpoints receive the authorized `Workspace`; every POST adds `dependencies=[Depends(require_csrf)]`. Convert `classification == "liability"` to a boolean only after rejecting values outside `{"asset", "liability"}`. Routes commit on success, roll back on service errors, return 422 with submitted values and `field_errors`, and translate `AccountNotFoundError` to a generic 404. Use `date.today()` only at the route boundary for manual-balance validation.

Import and include the router in `app/main.py` after the workspace router. Add Dashboard and Accounts navigation links when `workspace` is defined. Add matching workspace-home actions.

- [ ] **Step 4: Render accessible account forms and list**

`accounts/form.html` must contain labels for name, account type, institution, and asset/liability classification; preserve submitted values; display field-specific errors; include CSRF; and explain that liabilities use a positive amount owed.

`accounts/index.html` must show type, institution, classification, Edit, and Add
balance links. Current balances belong to the dashboard report introduced in
Task 5, so this first account-management page must not guess or duplicate the
later latest-snapshot query. It must have a truthful empty state linking to
`/accounts/new`.

`accounts/balance_form.html` must use `type="number" min="0" step="0.01"` for convenience while preserving server validation, `type="date" max="{{ today.isoformat() }}"`, CSRF, and clear asset/liability wording.

- [ ] **Step 5: Add failing CSRF, validation, and foreign-account tests**

Append route tests that assert:

```python
assert (await client.post(f"/workspaces/{workspace_id}/accounts", data={})).status_code == 403
```

Submit a blank name with a valid CSRF token and assert status 422, the message “Account name is required.”, and no new row. Create `SECRET OTHER ACCOUNT` in an unrelated workspace, then assert GET edit, GET balance form, POST edit, and POST balance all return 404 and never include the secret name/balance. Verify invalid `classification=maybe` returns 422 rather than treating it as an asset.

- [ ] **Step 6: Run account route/service tests and commit**

Run:

```powershell
uv run pytest tests/accounts -v
uv run ruff check app/accounts tests/accounts app/main.py
uv run ruff format --check app/accounts tests/accounts app/main.py
```

Expected: account tests pass; navigation changes do not break application imports.

Commit:

```powershell
git add app/accounts app/templates/accounts app/main.py app/templates/base.html app/templates/workspace_detail.html tests/accounts
git commit -m "feat: add account and balance forms"
```

---

### Task 3: Calculate current position and annual net worth

**Files:**
- Create: `app/dashboard/__init__.py`
- Create: `app/dashboard/types.py`
- Create: `app/dashboard/service.py`
- Create: `tests/dashboard/__init__.py`
- Create: `tests/dashboard/test_position.py`

**Interfaces:**
- Consumes: existing workspace-scoped accounts, snapshots, and transactions.
- Produces: `AccountPosition`, `PositionSummary`, `AnnualPosition`, `resolve_as_of_date()`, `get_position_summary()`, and `build_net_worth_series()`.

- [ ] **Step 1: Write failing current-position tests**

Create immutable expected-value tests:

```python
def test_position_uses_latest_eligible_snapshot_and_subtracts_liabilities(
    session: Session, workspace: Workspace
) -> None:
    checking = _account(session, workspace.id, "Checking", "checking", False)
    mortgage = _account(session, workspace.id, "Mortgage", "mortgage", True)
    missing = _account(session, workspace.id, "Brokerage", "investment_brokerage", False)
    _snapshot(session, workspace.id, checking.id, 800_000, date(2026, 7, 31))
    _snapshot(session, workspace.id, checking.id, 900_000, date(2026, 8, 10))
    _snapshot(session, workspace.id, checking.id, 950_000, date(2026, 8, 11))
    _snapshot(session, workspace.id, mortgage.id, 8_300_000, date(2026, 8, 10))
    session.commit()

    summary = get_position_summary(session, workspace.id, date(2026, 8, 10))

    assert summary.assets_cents == 900_000
    assert summary.liabilities_cents == 8_300_000
    assert summary.net_worth_cents == -7_400_000
    assert summary.cash_cents == 900_000
    assert summary.missing_balance_count == 1
    assert [item.account_id for item in summary.accounts] == [checking.id, missing.id, mortgage.id]
```

Add a same-day correction test with two snapshots and assert the larger `id` wins. Add a foreign workspace with a $99,999,999 balance and assert it never changes the requested totals.

- [ ] **Step 2: Run and confirm missing-dashboard-module failure**

Run `uv run pytest tests/dashboard/test_position.py -v`.

Expected: collection fails because `app.dashboard.service` does not exist.

- [ ] **Step 3: Define exact current-position result types and query algorithm**

In `app/dashboard/types.py` define frozen dataclasses:

```python
@dataclass(frozen=True)
class AccountPosition:
    account_id: int
    name: str
    account_type: str
    institution: str | None
    is_liability: bool
    balance_cents: int | None
    as_of_date: date | None


@dataclass(frozen=True)
class PositionSummary:
    assets_cents: int
    liabilities_cents: int
    net_worth_cents: int
    cash_cents: int
    missing_balance_count: int
    accounts: tuple[AccountPosition, ...]


@dataclass(frozen=True)
class AnnualPosition:
    year: int
    assets_cents: int | None
    liabilities_cents: int | None
    net_worth_cents: int | None
```

`get_position_summary()` first selects accounts filtered by workspace and ordered by account type, lowercase name, and ID. Select snapshots with both `AccountBalanceSnapshot.workspace_id == workspace_id` and eligible account IDs, filter `as_of_date <= cutoff`, then order by account ID, date descending, ID descending. Keep the first snapshot per account in Python. Sum known balances; preserve negative exceptional values exactly. Cash types are `checking` and `savings` only when `is_liability` is false.

- [ ] **Step 4: Add failing latest-date and five-year boundary tests**

Test `resolve_as_of_date()` with a latest transaction at 2026-08-09 and a snapshot at 2026-08-10, then assert 2026-08-10. Assert unrelated dates are ignored and no data returns `None`.

Seed snapshots from 2021 through 2026 and assert:

```python
series = build_net_worth_series(session, workspace.id, date(2026, 8, 10), years=5)
assert [point.year for point in series] == [2022, 2023, 2024, 2025, 2026]
assert series[-1].net_worth_cents == expected_2026
```

Prove a 2022 balance carries forward for an account with no 2023 update, a 2026-09 snapshot is excluded from the 2026 current point, and an entirely missing historical year uses `None` values rather than numeric zero.

- [ ] **Step 5: Implement data-driven cutoff and annual carry-forward**

`resolve_as_of_date()` returns the maximum of the workspace's `Transaction.date` calendar date and `AccountBalanceSnapshot.as_of_date`, or `None`. `build_net_worth_series()` emits exactly `years` ascending points, uses December 31 for completed years and the supplied cutoff for the current year, and calls the same snapshot-selection algorithm so current and historical totals cannot diverge.

- [ ] **Step 6: Run position tests and commit**

Run:

```powershell
uv run pytest tests/dashboard/test_position.py -v
uv run ruff check app/dashboard tests/dashboard
uv run ruff format --check app/dashboard tests/dashboard
```

Commit:

```powershell
git add app/dashboard tests/dashboard
git commit -m "feat: calculate workspace financial position"
```

---

### Task 4: Calculate cash flow and deterministic highlights

**Files:**
- Modify: `app/dashboard/types.py`
- Modify: `app/dashboard/service.py`
- Create: `app/dashboard/presentation.py`
- Create: `tests/dashboard/test_cash_flow.py`
- Create: `tests/dashboard/test_report.py`
- Create: `tests/dashboard/test_presentation.py`

**Interfaces:**
- Consumes: Task 3 position functions and categorized transactions.
- Produces: `AnnualCashFlow`, `DashboardHighlight`, `DashboardReport`, `build_cash_flow_series()`, `build_dashboard_report()`, `format_money()`, `format_basis_points()`, and `chart_payload()`.

- [ ] **Step 1: Write failing cash-flow classification tests**

Create `tests/dashboard/test_cash_flow.py` with built-in/workspace categories and signed transactions. Assert:

```python
series = build_cash_flow_series(session, workspace.id, date(2026, 8, 10), years=5)
current = series[-1]
assert current.income_cents == 500_000
assert current.spending_cents == 300_000
assert current.savings_cents == 200_000
assert current.savings_rate_basis_points == 4_000
assert current.needs_review_count == 3
```

The fixture must include: positive income; negative expense; positive transfer; negative transfer; a positive expense mismatch; a negative income mismatch; a transaction without a category; and one foreign-workspace transaction. Only the valid income and expense contribute. The three mismatch/missing-category rows count as needs review; transfers do not.

- [ ] **Step 2: Run and observe missing cash-flow symbols**

Run `uv run pytest tests/dashboard/test_cash_flow.py -v`.

Expected: import fails because `AnnualCashFlow` and `build_cash_flow_series` are not defined.

- [ ] **Step 3: Define cash-flow type and exact basis-point rounding**

Add:

```python
@dataclass(frozen=True)
class AnnualCashFlow:
    year: int
    income_cents: int | None
    spending_cents: int | None
    savings_cents: int | None
    savings_rate_basis_points: int | None
    needs_review_count: int
```

Query transactions with an outer join to `Category` so missing categories remain
visible for the review count, filtered by workspace and the five-year date
window ending at the cutoff. Group in Python by calendar year. Classify only
positive `income` and negative `expense`. Compute rate as
`(Decimal(income_cents - spending_cents) / Decimal(income_cents) *
Decimal(10_000)).quantize(Decimal("1"), ROUND_HALF_UP)`. If no valid
income/expense exists in a year, all money fields are `None`; if spending exists
without income, savings is negative spending and rate is `None`.

- [ ] **Step 4: Add failing report, repeatability, and highlight-priority tests**

In `tests/dashboard/test_report.py`, assert two calls with identical fixture/session/cutoff produce equal frozen reports. Define `DashboardReport` fields:

```python
@dataclass(frozen=True)
class DashboardHighlight:
    kind: str
    title: str
    detail: str
    tone: str


@dataclass(frozen=True)
class DashboardReport:
    as_of_date: date | None
    position: PositionSummary
    net_worth_series: tuple[AnnualPosition, ...]
    cash_flow_series: tuple[AnnualCashFlow, ...]
    highlights: tuple[DashboardHighlight, ...]
```

Assert at most three highlights with this fixed priority:

1. If at least two non-`None` net-worth points exist, emit improvement/decline/unchanged using the last two available points and exact delta.
2. If current-year valid income exists, emit saved amount and rate; mention prior-year rate change only when the prior year also has valid income.
3. If balances are missing, emit the missing count; otherwise emit the largest absolute known account position with ID as the tie-breaker.

If no data exists, assert `as_of_date is None`, empty series values remain truthful, and the first highlight is an empty-state setup message rather than invented money.

- [ ] **Step 5: Implement report assembly and formatting helpers**

`build_dashboard_report(session, workspace_id, as_of_date=None)` uses the explicit date when supplied; otherwise it calls `resolve_as_of_date()`. When no date exists, construct an empty position at `date.min` internally but return `as_of_date=None` and no numeric annual points.

In `presentation.py` implement signed `format_money(cents)` (`-$12.34`, never `$-12.34`), `format_basis_points(2480)` (`24.8%`), and `chart_payload(report)`. Payload keys are exactly:

```python
{
    "net_worth": {
        "labels": ["2022", "2023", "2024", "2025", "2026"],
        "values": [12_400_000, 15_300_000, 18_700_000, 25_300_000, 28_462_000],
    },
    "cash_flow": {
        "labels": ["2022", "2023", "2024", "2025", "2026"],
        "income": [6_500_000, 7_000_000, 7_500_000, 8_000_000, 5_400_000],
        "spending": [4_900_000, 5_100_000, 5_400_000, 5_800_000, 3_700_000],
    },
}
```

No account names, institutions, descriptions, user identifiers, or workspace identifiers enter chart JSON.

- [ ] **Step 6: Add presentation-boundary tests and run the dashboard service suite**

Assert signed dollar/percentage boundaries, payload `None` preservation, chronological labels, and the absence of strings seeded as `SECRET INSTITUTION` and `SECRET DESCRIPTION`.

Run:

```powershell
uv run pytest tests/dashboard/test_cash_flow.py tests/dashboard/test_report.py tests/dashboard/test_presentation.py -v
uv run ruff check app/dashboard tests/dashboard
uv run ruff format --check app/dashboard tests/dashboard
```

Commit:

```powershell
git add app/dashboard tests/dashboard
git commit -m "feat: calculate dashboard trends and highlights"
```

---

### Task 5: Render the authorized dashboard with HTML fallbacks

**Files:**
- Create: `app/dashboard/routes.py`
- Create: `app/templates/dashboard/index.html`
- Create: `tests/dashboard/test_routes.py`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/templates/workspace_detail.html`

**Interfaces:**
- Consumes: Task 4 complete report, presentation helpers, and existing authorization.
- Produces: named route `dashboard` at `GET /workspaces/{workspace_id}/dashboard` and bounded escaped chart JSON.

- [ ] **Step 1: Write failing authentication, empty-state, and exact-total route tests**

Use `build_route_test_app()` to assert unauthenticated requests redirect to `/`. After sign-in, an empty personal workspace returns 200 with “Add your first account” and “Import transactions” actions.

Seed checking $8,420, savings $16,420, brokerage $116,600, 401(k) $226,310, mortgage owed $83,130, and categorized 2026 transactions. Request `?as_of=2026-08-10` and assert the response contains:

```text
$367,750.00 assets
$83,130.00 liabilities
$284,620.00 net worth
$24,840.00 cash available
```

Assert both chart fallback tables contain five ascending years and exact money text.

- [ ] **Step 2: Run and confirm the missing-dashboard-route failure**

Run `uv run pytest tests/dashboard/test_routes.py -v`.

Expected: authorized dashboard request returns 404.

- [ ] **Step 3: Implement strict optional as-of parsing and route registration**

The route accepts `as_of: str = ""`. Blank means data-driven default. Nonblank must match `YYYY-MM-DD` exactly and parse with `date.fromisoformat`; invalid values render the dashboard shell with status 422 and “Use a valid date in YYYY-MM-DD format.” Do not query report data until workspace authorization has succeeded.

Pass `report`, `chart_payload(report)`, `format_money`, `format_basis_points`, account-type labels, CSRF, user, and workspace to Jinja. Register the router in `app/main.py`. Add Dashboard/Accounts links to the workspace page and the signed-in workspace navigation.

- [ ] **Step 4: Build semantic dashboard markup from the approved hybrid design**

Render:

- compact workspace-aware top navigation inherited from `base.html`;
- heading and explicit as-of date;
- net-worth hero with change highlight;
- four numeric metrics: assets, liabilities, cash, savings rate;
- fallback net-worth and income/spending tables;
- account-position list with “Balance not added” rows;
- deterministic highlight cards; and
- no-data and partial-data notices.

Embed payload only through:

```html
<script id="dashboard-chart-data" type="application/json">{{ chart_data | tojson }}</script>
```

Do not mark JSON safe manually. Canvas elements receive `role="img"`, concise `aria-label`, and adjacent fallback tables that are visible until JavaScript succeeds.

- [ ] **Step 5: Add cross-workspace and unsafe-payload route tests**

Create a second workspace containing account/institution/transaction strings beginning `SECRET OTHER`. Assert a nonmember dashboard request returns 404 and an authorized dashboard response contains none of those strings. Assert `application/json` payload has only the documented aggregate keys. Test invalid `as_of`, a cutoff before any balances, missing balances, negative net worth, zero income, and a future explicit cutoff without a server error.

- [ ] **Step 6: Run route, service, and existing auth tests; commit**

Run:

```powershell
uv run pytest tests/dashboard tests/test_auth_routes.py tests/test_workspaces.py -v
uv run ruff check app/dashboard tests/dashboard app/main.py
uv run ruff format --check app/dashboard tests/dashboard app/main.py
```

Commit:

```powershell
git add app/dashboard app/templates/dashboard app/main.py app/templates/base.html app/templates/workspace_detail.html tests/dashboard
git commit -m "feat: render the financial dashboard"
```

---

### Task 6: Add local Chart.js and the approved responsive visual system

**Files:**
- Create: `app/static/vendor/chartjs/chart.umd.min.js`
- Create: `app/static/vendor/chartjs/LICENSE.md`
- Create: `app/static/vendor/chartjs/README.md`
- Create: `app/static/dashboard.js`
- Modify: `app/static/styles.css`
- Modify: `app/templates/base.html`
- Modify: `app/templates/dashboard/index.html`
- Create: `tests/dashboard/test_static_assets.py`

**Interfaces:**
- Consumes: Task 5 JSON/fallback markup.
- Produces: offline line/bar charts and the approved calm command-center styling.

- [ ] **Step 1: Write failing local-asset and no-CDN tests**

Assert:

```python
def test_chartjs_is_pinned_local_and_licensed() -> None:
    vendor = Path("app/static/vendor/chartjs")
    assert (vendor / "chart.umd.min.js").is_file()
    assert "4.5.1" in (vendor / "README.md").read_text(encoding="utf-8")
    assert "MIT License" in (vendor / "LICENSE.md").read_text(encoding="utf-8")


def test_dashboard_assets_do_not_load_a_cdn() -> None:
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "app/templates/dashboard/index.html",
            "app/static/dashboard.js",
        )
    )
    assert "https://" not in sources
    assert "/static/vendor/chartjs/chart.umd.min.js" in sources
    assert "/static/dashboard.js" in sources
```

- [ ] **Step 2: Run and confirm missing static assets**

Run `uv run pytest tests/dashboard/test_static_assets.py -v`.

Expected: missing `app/static/vendor/chartjs/chart.umd.min.js` and companion files.

- [ ] **Step 3: Vendor Chart.js 4.5.1 with provenance**

Download the official npm package `chart.js@4.5.1`, copy only `dist/chart.umd.min.js` and `LICENSE.md`, and write `README.md` containing version `4.5.1`, source package name, official project URL, retrieval date, and SHA-256 of the committed UMD file. Do not add `node_modules`, `package.json`, a lock file, or a runtime CDN reference.

- [ ] **Step 4: Implement progressive chart initialization**

`dashboard.js` must:

1. return without throwing if the JSON element, canvas elements, or global `Chart` are missing;
2. parse the one bounded JSON element;
3. read dashboard colors from CSS custom properties;
4. create one responsive line chart for net worth and one grouped bar chart for income/spending;
5. format tooltip cents as signed dollar strings in JavaScript without changing source data;
6. set `animation: false` when `matchMedia("(prefers-reduced-motion: reduce)").matches`;
7. add `dashboard-charts-ready` to the dashboard root only after both charts initialize; and
8. leave fallback tables visible on any exception.

- [ ] **Step 5: Implement the approved responsive CSS and base template hooks**

Add `{% block head %}` before `</head>` and `{% block scripts %}` before `</body>` in `base.html`; existing pages render unchanged. Dashboard template loads the local Chart.js file followed by `dashboard.js` with `defer`.

Expand CSS with `--dashboard-*` theme colors, top navigation, hero, metric grid, two-column chart/account grid, highlight strip, chart containers, focus states, and visible fallbacks. At `max-width: 42.5rem`, stack every dashboard section into one column, hide nonessential nav labels, keep text at least 0.75rem, and prohibit fixed widths that cause overflow. Only `.dashboard-charts-ready` may visually hide the fallback tables, using a reusable visually-hidden pattern rather than `display:none` so assistive technology retains the data.

- [ ] **Step 6: Verify browser behavior at desktop and mobile widths**

Start the app with synthetic test data or a test-only rendered page and inspect at 1024, 736, and 360 CSS pixels. At each width verify:

- no horizontal overflow;
- no clipped labels, values, legends, or account names;
- both charts contain five years and tooltips show dollars;
- keyboard focus is visible;
- disabling JavaScript shows both fallback tables; and
- emulated reduced motion disables chart animation.

Capture local screenshots for review but do not commit environment-specific screenshots unless the user explicitly requests them.

- [ ] **Step 7: Run static/route tests and commit**

Run:

```powershell
uv run pytest tests/dashboard/test_static_assets.py tests/dashboard/test_routes.py -v
uv run ruff check app tests/dashboard
uv run ruff format --check app tests/dashboard
```

Commit:

```powershell
git add app/static/vendor/chartjs app/static/dashboard.js app/static/styles.css app/templates/base.html app/templates/dashboard/index.html tests/dashboard/test_static_assets.py
git commit -m "feat: polish dashboard charts and layout"
```

---

### Task 7: Add a fixed synthetic demo and end-to-end acceptance test

**Files:**
- Create: `app/dashboard/demo_data.json`
- Create: `app/dashboard/demo.py`
- Create: `tests/dashboard/test_demo.py`
- Create: `tests/dashboard/test_acceptance.py`

**Interfaces:**
- Consumes: account/dashboard services, existing user/workspace models, built-in categories, and application settings.
- Produces: `seed_dashboard_demo(session, user) -> Workspace` and CLI `uv run python -m app.dashboard.demo --email EMAIL`.

- [ ] **Step 1: Create the fixed fictional fixture and failing seed test**

The JSON fixture contains:

- accounts: Everyday Checking, Emergency Savings, Example 401(k), Example Brokerage, and Home Mortgage;
- annual snapshots dated December 31 for 2022–2025 plus August 10, 2026;
- categorized annual income and expense transactions sufficient to produce all five cash-flow bars; and
- exact expected 2026 totals: assets 36,775,000 cents, liabilities 8,313,000 cents, net worth 28,462,000 cents, and cash 2,484,000 cents.

Write a test that loads the packaged fixture, calls
`seed_dashboard_demo(session, user)`, then calls
`build_dashboard_report(session, workspace.id, date(2026, 8, 10))` and asserts
every expected total and all five years. Call the seed function again and
assert a `DemoAlreadyExistsError` with no second workspace or duplicated
transactions.

- [ ] **Step 2: Run and confirm missing demo module**

Run `uv run pytest tests/dashboard/test_demo.py -v`.

Expected: collection fails for missing `app.dashboard.demo`.

- [ ] **Step 3: Implement explicit, atomic, non-overwriting demo seeding**

Define constants `DEMO_WORKSPACE_NAME = "Dashboard Demo"` and the packaged fixture path. `seed_dashboard_demo()` first checks a membership-visible workspace with that exact name for the user; if present, raise without mutation. Otherwise use `create_household_workspace()`, resolve required built-in categories by exact name, add accounts/snapshots/transactions with only the new workspace ID, call `session.flush()`, and return the workspace. The caller controls commit/rollback.

The CLI requires `--email`, initializes the configured database, looks up an existing normalized user email, prints a beginner-friendly error if the user has not signed in once, commits only after complete seeding, and prints the dashboard URL. It never creates a Google identity, overwrites a workspace, or logs balances from another workspace.

- [ ] **Step 4: Write the real HTTP acceptance test**

Sign in through the test app, seed the signed-in user, request the returned workspace dashboard with `?as_of=2026-08-10`, and assert:

- all four exact metrics;
- five net-worth years and five cash-flow years;
- the expected account names but no raw foreign-workspace fixture value;
- local Chart.js and dashboard script URLs;
- factual highlight copy; and
- a nonmember receives 404 for both the demo accounts and dashboard routes.

Repeat `build_dashboard_report()` twice and assert object equality and identical `chart_payload()` dictionaries.

- [ ] **Step 5: Run demo/acceptance/full dashboard tests and commit**

Run:

```powershell
uv run pytest tests/dashboard -v
uv run ruff check app/dashboard tests/dashboard
uv run ruff format --check app/dashboard tests/dashboard
```

Commit:

```powershell
git add app/dashboard/demo.py app/dashboard/demo_data.json tests/dashboard/test_demo.py tests/dashboard/test_acceptance.py
git commit -m "test: add deterministic dashboard demo"
```

---

### Task 8: Document the dashboard and perform release-quality verification

**Files:**
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Review: all PR 7 files and commits

**Interfaces:**
- Consumes: complete account/dashboard implementation and demo CLI.
- Produces: beginner instructions, verified branch, pushed branch, and a ready pull request.

- [ ] **Step 1: Update beginner-facing README instructions**

Document:

1. sign in once and choose a workspace;
2. open Accounts, create asset/liability accounts, and enter positive balances/amounts owed with dates;
3. open Dashboard and understand assets, liabilities, net worth, cash, savings rate, missing balances, and five-year charts;
4. run `uv run python -m app.dashboard.demo --email your-google-email@example.com` to create the isolated synthetic “Dashboard Demo” workspace;
5. open the printed URL and remove the demo later only through a future supported deletion flow—do not suggest manual database deletion;
6. Chart.js is stored and served locally, while dashboard calculations use no AI or network call; and
7. LangGraph remains scheduled for PR 10's optional financial coach.

Mark PR 7 implemented in the breakdown only after all verification succeeds. Update the project map with `app/accounts/` and `app/dashboard/`.

- [ ] **Step 2: Run all quality gates fresh**

Run:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Expected: all commands exit zero. Record exact test count and duration for the PR body.

- [ ] **Step 3: Verify unchanged migrations and development startup**

Create a fresh temporary SQLite path, set `DATABASE_URL`, run `uv run alembic
upgrade head`, `uv run alembic current`, downgrade to
`0006_builtin_categories`, and upgrade head again. Then start the application
lifespan against the fresh database with a TestClient and assert `/health`
returns `{"status": "ok"}`. Expected Alembic head remains
`0008_unique_payslip_income`: merged PR 6 added that uniqueness migration, while
PR 7 itself adds no migration.

- [ ] **Step 4: Audit privacy, dependencies, and repository scope**

Run:

```powershell
git diff --check
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff main...HEAD
rg -n "https://|langgraph|openai|anthropic|workspace_id" app/accounts app/dashboard app/templates/dashboard app/static/dashboard.js
rg -n "SECRET|storage_path|checksum|description" app/templates/dashboard app/static/dashboard.js
```

Expected: no CDN/model dependency; every database-facing module contains explicit workspace scoping; templates/JavaScript contain no private raw fields; diff contains only PR 7 account/dashboard/test/docs/vendor work; and no real financial or identity fixture exists.

- [ ] **Step 5: Re-run responsive visual QA after final CSS**

Load the synthetic demo and inspect 1024, 736, 360, light appearance, dark appearance where supported, JavaScript disabled, and reduced motion. Fix any clipping, overflow, illegible contrast, chart/fallback mismatch, or keyboard issue, then rerun Steps 2–4.

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md docs/where-is-my-money-pr-breakdown.md
git commit -m "docs: explain the financial dashboard"
```

- [ ] **Step 7: Request final code review and fix findings test-first**

Invoke `superpowers:requesting-code-review`, compare the implementation against the approved spec and this plan, address every valid finding with a failing regression test first, then rerun Steps 2–5.

- [ ] **Step 8: Push and open the ready pull request without merging**

Push `codex/pr-7-financial-dashboard` to `origin`. Create a ready PR targeting `main` titled `PR 7 — centralized financial dashboard`. The body must summarize account/manual-balance flows, deterministic calculations, workspace/CSRF isolation, local Chart.js/privacy, responsive accessibility, synthetic demo steps, exact Pytest/Ruff results, and migration/startup verification. Do not merge the PR.

## Plan self-review result

- Spec coverage: account setup, manual balances, current position, cash flow, five-year trends, deterministic highlights, authorization, partial/empty states, local charts, responsive/accessibility behavior, demo, docs, and verification each map to a named task.
- Placeholder scan: passed; every step names its behavior, error handling, and test evidence.
- Type consistency: `AccountInput`, `ManualBalanceInput`, `PositionSummary`, `AnnualPosition`, `AnnualCashFlow`, `DashboardHighlight`, and `DashboardReport` have one definition and the same names in every consumer.
- Scope: PR 6 is present only as the merged base; payslip data is not consumed. PR 8 goals/budgets, PR 8b statement extraction, PR 10 LangGraph, bank APIs, deletion, and money movement remain outside PR 7.
