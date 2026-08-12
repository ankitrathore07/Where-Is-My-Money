# PR 7 — Centralized Financial Dashboard Design

**Date:** 2026-08-10

**Status:** Approved for implementation planning

**Branch base:** merged PRs 1–6

**Functional dependencies:** merged PRs 1–5 only

**Does not consume:** PR 6 payslip data, budgets/goals, statement balance
extraction, bank APIs, LangGraph, or an LLM

## Purpose

PR 7 gives each authorized workspace one useful, attractive place to answer four
basic questions:

1. How much do I own?
2. How much do I owe?
3. Where is my money held?
4. Is my financial position improving over time?

The feature also adds the minimum account and manual balance screens needed to
make that dashboard usable immediately. Later pull requests can add statement
extraction, budgets, goals, bank connections, and an AI financial coach without
rewriting this foundation.

## Product direction

The approved visual direction combines:

- the information density of a financial “command center”;
- the calm green palette, rounded surfaces, and clear hierarchy of the first
  dashboard concept; and
- short, plain-language story highlights that explain progress over time.

The desktop layout uses a compact top navigation, a prominent net-worth trend,
four key metrics, a five-year income-versus-spending chart, an account-position
list, and three deterministic story highlights. On narrow screens, the same
content becomes one readable column without horizontal scrolling.

The dashboard must look polished with synthetic data, remain truthful with
partial data, and provide helpful empty states rather than invented figures.

## Technology choice

Keep the existing application stack:

- FastAPI routes and authorization dependencies;
- SQLAlchemy queries and services;
- Jinja server-rendered HTML;
- the existing CSS file, expanded into a small dashboard design system;
- Chart.js for responsive, accessible charts; and
- a small amount of plain JavaScript for chart initialization and period
  selection.

Chart.js will be stored under the application's static directory with its
license instead of loaded from a CDN. This preserves offline development,
avoids sending browser metadata to a third party, and does not require Node,
React, or a separate frontend build.

Plotly Dash is not selected because it would create a second web-application
model beside FastAPI and Jinja. React is not selected because it would add an
API/frontend/build-tool boundary that the current product does not need. D3 is
not selected because its extra flexibility is not needed for these standard
financial charts and would be harder for a new contributor to maintain.

## Scope

### Account setup

An authorized workspace member can:

- create an account with a name, type, optional institution, and asset/liability
  classification;
- edit those descriptive fields;
- list only accounts in the active workspace; and
- add a dated manual balance snapshot.

Supported account types are checking, savings, credit card, 401(k), brokerage,
mortgage, auto loan, student loan, and other. Checking, savings, 401(k), and
brokerage default to assets. Credit card, mortgage, auto loan, and student loan
default to liabilities. “Other” requires the member to choose. The server
validates the final classification; a browser default is only a convenience.

Balance input is entered as a non-negative dollar amount. For an asset it means
“money/value owned.” For a liability it means “amount still owed.” This avoids
asking a beginner whether a credit-card balance should be typed as positive or
negative. Values are converted once to integer cents and never stored as binary
floating-point dollars.

Editing an account never rewrites its historical balance snapshots. Removing
accounts or snapshots is outside this PR because deletion semantics need a
separate design.

### Dashboard

Add an authorized route at:

```text
GET /workspaces/{workspace_id}/dashboard
```

The workspace home links to the dashboard. The dashboard uses the same
membership-derived `require_workspace` dependency as imports, categories, and
transactions. No workspace identifier is accepted from a query string, form
field, chart payload, or client-side script.

The default “as of” date is the latest date present in either a workspace
transaction or account balance snapshot. If no financial data exists, the page
shows account/import setup actions. The service also accepts an explicit
`as_of_date` for deterministic tests and future period controls.

### Current-position metrics

For every account, select its latest snapshot on or before the report's as-of
date. Do not select a newer snapshot and do not select any snapshot through a
different workspace.

- **Assets:** sum the latest balances of non-liability accounts.
- **Liabilities:** sum the latest balances of liability accounts.
- **Net worth:** assets minus liabilities.
- **Cash available:** sum checking and savings asset balances.

Accounts without an eligible snapshot appear as “Balance not added” and do not
silently contribute zero to totals. The dashboard states how many accounts are
missing balances whenever the current position is incomplete.

### Transaction metrics

Only committed transactions in the active workspace are used.

Category kind determines how a transaction contributes:

- income is a positive transaction whose category kind is `income`;
- spending is the absolute value of a negative transaction whose category kind
  is `expense`;
- transfer categories are excluded from both; and
- sign/category mismatches are excluded from rate calculations and counted in
  a small “needs review” indicator rather than guessed.

For a calendar period:

- **saved amount** = income minus spending;
- **savings rate** = saved amount divided by income; and
- when income is zero, savings rate is unavailable rather than zero or infinity.

Percentages are derived from integer cents and rounded once for display using
decimal arithmetic. The underlying service result retains integer cents.

### Trends

The net-worth chart contains up to five annual points ending in the as-of year.
For each year end, select the latest snapshot on or before that cutoff for every
workspace account. A prior balance may carry forward until a newer snapshot is
recorded. The current year uses the selected as-of date rather than December 31.

The income-versus-spending chart uses the same five calendar years and the
transaction rules above. Years with no transactions are shown as missing/empty,
not as proof of zero activity.

All sequences have stable chronological ordering. Account lists use account
type, case-insensitive name, then ID as a deterministic tie-breaker.

### Deterministic story highlights

PR 7 does not use AI to invent advice. It may render up to three short factual
highlights from the same calculated report:

1. net-worth change from the prior available annual point;
2. current-year savings and savings-rate change when comparable prior-year
   income data exists; and
3. the largest current account position or the number of accounts missing a
   balance.

Each highlight includes the period and exact numbers that produced it. Budget
progress, goal projections, recurring-charge detection, unusual-spend advice,
and “where to cut” recommendations belong to later PRs.

## Components and boundaries

```text
authorized route
    -> account service (validated writes)
    -> dashboard query service (workspace-scoped reads)
    -> immutable report types (integer cents and dates)
    -> Jinja presentation adapter (formatted dollars and chart JSON)
    -> local Chart.js renderer
```

Implementation modules:

- `app/accounts/types.py`: supported types and validated command values;
- `app/accounts/service.py`: scoped account creation/edit and manual snapshots;
- `app/accounts/routes.py`: thin authorized HTML routes;
- `app/dashboard/types.py`: immutable report result types;
- `app/dashboard/service.py`: current-position and trend calculations;
- `app/dashboard/routes.py`: authorized report route and template context;
- `app/templates/accounts/`: list, create/edit, and balance forms;
- `app/templates/dashboard/index.html`: approved dashboard layout; and
- `app/static/vendor/`: pinned Chart.js distribution and license.

The dashboard service accepts a SQLAlchemy session, workspace ID, and explicit
as-of date. It returns data and never commits. Routes authorize first, then call
the service. Account mutation services scope both the parent account and new
snapshot to the same workspace before committing.

## Data and migrations

The existing `accounts` and `account_balance_snapshots` tables provide the core
schema. Before adding a snapshot, the service verifies that
`account.workspace_id == active_workspace.id` and always copies that trusted
workspace ID to the snapshot.

No migration is planned because the existing account and balance-snapshot schema
supports this feature. Non-negative manual balances are enforced by the account
service. PR 7 must not change payslip, budget, savings-goal, or insight-snapshot
schema or behavior.

No dashboard snapshot table is required. Reports are deterministic projections
from transactions and balance snapshots. The existing `insight_snapshots` table
remains unused until the later AI/insight design defines its versioned payload.

## Validation and error handling

- Account names are normalized for surrounding/repeated whitespace, required,
  and limited to the model's 255-character boundary.
- Institutions are optional, normalized the same way, and bounded.
- Account type and asset/liability values must be from explicit allowlists.
- Manual dollar values accept at most two decimal places, are non-negative, and
  must fit the database integer range.
- Snapshot dates must be valid ISO calendar dates and cannot be later than the
  current UTC calendar date. The service accepts that current date as an
  injectable value so boundary tests do not depend on the machine clock.
- Validation failures return the form with field-specific messages and no
  partial database write.
- Missing and foreign account IDs both return 404 without revealing names,
  institutions, balances, or existence.
- A dashboard with no or partial data renders safe empty/incomplete states; it
  does not fail or manufacture values.
- Chart initialization failure leaves the numeric summaries and account list
  readable in HTML.

## Security and privacy

Every database query begins with the active workspace boundary. Route tests must
prove that a member cannot view or mutate another workspace's accounts,
snapshots, dashboard totals, chart JSON, empty-state messages, or validation
responses.

All account and balance mutations require the existing CSRF dependency. Values
are rendered through Jinja escaping. Chart data contains only bounded aggregate
series and labels needed by the visible dashboard; raw transaction descriptions
are not embedded in JavaScript.

Chart.js is served locally. PR 7 makes no model, analytics, telemetry, or other
network call and requires no new secret.

## Accessibility and responsive behavior

- Numeric facts remain real HTML text, not chart-only pixels.
- Charts have visible titles, legends, units, and concise screen-reader
  descriptions.
- Color is paired with labels; asset/liability and income/spending meaning never
  depends on hue alone.
- Keyboard focus remains visible and native controls have explicit labels.
- The dashboard has no horizontal overflow at 360 CSS pixels.
- Reduced-motion preferences disable nonessential chart animation.
- Dollar formatting uses a consistent sign and clear “owed” wording for
  liabilities.

## Test strategy

Use only fictional users, institutions, accounts, transactions, and balances.

### Service tests

- account type defaults and server-side validation;
- exact decimal-to-cents boundaries;
- creation/edit/snapshot workspace scoping;
- latest eligible snapshot per account;
- assets, liabilities, net worth, and cash calculations;
- transfer and sign/category mismatch exclusion;
- savings amount/rate including zero-income behavior;
- five-year cutoff, carry-forward, missing-year, and year-boundary cases;
- stable ordering and identical repeated output; and
- partial/missing balance reporting.

### Route and authorization tests

- unauthenticated redirect behavior;
- member access and nonmember 404 behavior;
- CSRF enforcement for every mutation;
- foreign account mutation rejection;
- safe validation responses without cross-workspace names or balances;
- dashboard aggregate and chart-payload isolation; and
- responsive semantic template landmarks and accessible chart fallbacks.

### Acceptance fixture

Add a fixed synthetic household with checking, savings, 401(k), brokerage,
mortgage, five years of annual balance snapshots, and categorized transactions.
The acceptance test creates/updates balances through the real service or routes,
opens the dashboard, and asserts the exact net worth, assets, liabilities, cash,
savings rate, five-year series, and factual highlights. Repeating the report for
the same as-of date must produce identical data.

## Demo and completion criteria

PR 7 is complete when:

- a new member can create accounts and enter manual balances;
- the approved responsive dashboard renders truthful current and five-year
  financial position from those balances and categorized transactions;
- fixed fixture data yields repeatable exact totals and chart series;
- authorization tests prove all account and dashboard data is workspace-scoped;
- Ruff lint, Ruff format check, the full Pytest suite, and migration/startup
  verification pass; and
- the README contains beginner-friendly steps for loading or entering synthetic
  demo data and opening the dashboard after the PR is merged.

## Explicit non-goals

- LangGraph, an LLM, prompts, or conversational UI;
- payslip extraction or PR 6 code;
- budget editing, goal creation, or goal projections;
- recurring-charge or unusual-spend advice;
- CSV/PDF account-statement balance extraction;
- direct bank connections;
- account or snapshot deletion;
- market-price lookup or investment-performance calculations; and
- money movement or automated financial decisions.

## Roadmap handoff

PR 8 keeps budgets and savings goals. PR 8b uses the PR 7 account service to add
reviewed CSV/PDF balance extraction. PR 10 introduces the LangGraph financial
coach only after dashboard, account, budget, and goal tools exist. That coach may
answer questions, compare spending, and draft a goal plan. Any goal write must
show the exact proposed change and require an explicit human confirmation; it
will never move money.
