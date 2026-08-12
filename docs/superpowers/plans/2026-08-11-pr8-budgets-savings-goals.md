# PR 8 Budgets and Savings Goals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an authorized planning center with explicit monthly budget acceptance, remaining-spend reporting, and deterministic savings-goal projections.

**Architecture:** Add a cohesive `app/planning/` feature package with immutable report/input types, pure integer-cent calculations, workspace-scoped SQLAlchemy services, server-rendered routes, and Jinja templates. Reuse the existing PR7 authorization, CSRF, navigation, and form conventions; keep derived goal values out of persistence so calculation direction remains explainable.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, SQLite/PostgreSQL-compatible queries, Jinja2, Alembic, Pytest, Ruff.

## Global Constraints

- Suggested monthly category limit is the median of the prior three complete months plus a 10% buffer.
- A GET never creates a budget; the member must explicitly accept or edit a suggestion.
- Money uses integer cents and deterministic half-up or ceiling arithmetic, never binary floating point.
- Savings goals persist exactly one supplied planning input and calculate the missing target date or monthly contribution.
- Every database query and mutation is scoped to the authorized workspace.
- PR8b extraction/uploads, LangGraph/AI, bank connections, and money movement remain out of scope.
- Existing migration `0004_planning_insights` already supplies the required columns; this PR adds no migration unless a failing test proves the schema insufficient.

---

### Task 1: Calculate and persist monthly budgets

**Files:**
- Create: `app/planning/__init__.py`
- Create: `app/planning/types.py`
- Create: `app/planning/service.py`
- Create: `tests/planning/__init__.py`
- Create: `tests/planning/test_budgets.py`

**Interfaces:**
- Consumes: `Budget`, `Category`, and `Transaction` models; accessible built-in categories have `workspace_id is None` and custom categories match the active workspace.
- Produces: `BudgetSuggestion`, `BudgetLine`, `BudgetMonthReport`, `PlanningValidationError`, `parse_money_to_cents()`, `build_budget_month_report()`, and `save_budget()`.

- [ ] **Step 1: Write failing suggestion tests**

Seed an accessible expense category with May/June/July totals of 10,000, 30,000,
and 20,001 cents. Assert this fixed interface:

```python
report = build_budget_month_report(session, workspace.id, date(2026, 8, 1))
line = report.lines[0]
assert report.source_start == date(2026, 5, 1)
assert report.source_end == date(2026, 7, 31)
assert line.suggestion is not None
assert line.suggestion.monthly_spend_cents == (10_000, 30_000, 20_001)
assert line.suggestion.median_cents == 20_001
assert line.suggestion.suggested_cents == 22_001
```

Add tests proving a missing month contributes zero, the January window crosses
the year boundary, and income/transfer/unassigned/foreign/`Uncategorized`
transactions do not enter suggestions.

- [ ] **Step 2: Run the focused test and confirm import failure**

Run: `uv run pytest tests/planning/test_budgets.py -v`

Expected: collection fails because `app.planning.service` does not exist.

- [ ] **Step 3: Define budget report types and month helpers**

Create immutable dataclasses with these fields:

```python
@dataclass(frozen=True)
class BudgetSuggestion:
    monthly_spend_cents: tuple[int, int, int]
    median_cents: int
    suggested_cents: int

@dataclass(frozen=True)
class BudgetLine:
    category_id: int
    category_name: str
    budget_id: int | None
    limit_cents: int | None
    spent_cents: int
    remaining_cents: int | None
    suggestion: BudgetSuggestion | None

@dataclass(frozen=True)
class BudgetMonthReport:
    period_month: date
    source_start: date
    source_end: date
    lines: tuple[BudgetLine, ...]
```

Implement `_month_start()`, `_shift_month()`, and `_month_end()` without
third-party dependencies.

- [ ] **Step 4: Implement the workspace-scoped suggestion query**

Query transactions from `source_start` inclusive through the selected month
exclusive, join the category, and require:

```python
Transaction.workspace_id == workspace_id
Transaction.amount_cents < 0
Category.kind == "expense"
or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id)
func.lower(Category.name) != "uncategorized"
```

Aggregate positive spending cents into three month buckets per category. Use
`sorted(totals)[1]` for the median and `(median * 110 + 50) // 100` for the 10%
half-up buffered suggestion. Query accepted budgets for the selected month and
selected-month spending separately, merge categories present in either set,
sort by case-insensitive category name then ID, and preserve negative remaining
values.

- [ ] **Step 5: Add failing explicit-save and validation tests**

Assert:

```python
assert parse_money_to_cents("220.01", field="amount") == 22_001
budget = save_budget(session, workspace.id, category.id, date(2026, 8, 1), 22_001)
updated = save_budget(session, workspace.id, category.id, date(2026, 8, 1), 21_500)
assert updated.id == budget.id
assert updated.amount_cents == 21_500
```

Reject zero, negative, more than two decimals, nonnumeric input, a non-first
period date, income/transfer categories, and another workspace's custom
category. Assert building a report alone leaves the budget table empty.

- [ ] **Step 6: Implement parsing, category authorization, and budget upsert**

`PlanningValidationError` carries `field_errors: dict[str, str]`.
`parse_money_to_cents()` uses `Decimal`, rejects exponent notation and more than
two fractional digits, and returns integer cents. `save_budget()` authorizes an
expense category with an explicit built-in-or-workspace predicate, loads an
existing budget by the unique workspace/category/month key, creates or updates
it, calls `session.flush()`, and leaves commit control to the route.

- [ ] **Step 7: Run budget tests and quality checks**

Run:

```powershell
uv run pytest tests/planning/test_budgets.py -v
uv run ruff check app/planning tests/planning/test_budgets.py
uv run ruff format --check app/planning tests/planning/test_budgets.py
```

Expected: all pass.

- [ ] **Step 8: Commit the budget service slice**

```powershell
git add app/planning tests/planning
git commit -m "feat: calculate explicit monthly budgets"
```

---

### Task 2: Calculate and persist savings goals

**Files:**
- Modify: `app/planning/types.py`
- Modify: `app/planning/service.py`
- Create: `tests/planning/test_goals.py`

**Interfaces:**
- Consumes: `SavingsGoal`, `PlanningValidationError`, integer-cent money parser, and month helpers from Task 1.
- Produces: `GoalInput`, `GoalProjection`, `create_goal()`, `update_goal()`, `get_workspace_goal()`, `project_goal()`, and `list_goal_projections()`.

- [ ] **Step 1: Write failing travel-goal projection tests**

Use a fixed `as_of_date=date(2026, 8, 11)`. A $5,000 target with $1,000 current
savings and a December 31, 2026 deadline has five contribution months and needs
$800.00 per month:

```python
projection = project_goal(goal, date(2026, 8, 11))
assert projection.remaining_cents == 400_000
assert projection.contribution_months == 5
assert projection.calculated_monthly_contribution_cents == 80_000
assert projection.calculated_target_date is None
assert projection.status == "on_track"
```

The inverse goal with an $800 monthly contribution calculates December 31,
2026. Add cross-year, non-even division, same-month, completed, and overdue
examples.

- [ ] **Step 2: Run the focused goal test and confirm missing symbols**

Run: `uv run pytest tests/planning/test_goals.py -v`

Expected: import fails for the goal interfaces.

- [ ] **Step 3: Define goal types and pure projections**

```python
@dataclass(frozen=True)
class GoalInput:
    name: str
    target_amount_cents: int
    current_amount_cents: int
    target_date: date | None
    monthly_contribution_cents: int | None

@dataclass(frozen=True)
class GoalProjection:
    goal_id: int
    name: str
    target_amount_cents: int
    current_amount_cents: int
    remaining_cents: int
    supplied_target_date: date | None
    supplied_monthly_contribution_cents: int | None
    calculated_target_date: date | None
    calculated_monthly_contribution_cents: int | None
    contribution_months: int | None
    status: str
```

Use `(remaining + divisor - 1) // divisor` for ceiling division. For a supplied
deadline, count inclusive calendar months only when the deadline is not before
the as-of date. For a supplied contribution, shift by `required_months - 1` and
use the final month's last day.

- [ ] **Step 4: Add failing goal validation, CRUD, and isolation tests**

Assert create/edit round trips preserve exactly one non-null planning input.
Reject blank or over-255-character names, nonpositive targets, negative current
savings, missing/both planning inputs, nonpositive contribution, and a deadline
before the as-of date on creation. Assert foreign goal lookup/update uses
`GoalNotFoundError` and does not mutate the foreign row. Repeated projection
calls must return equal values.

- [ ] **Step 5: Implement goal validation and workspace-scoped CRUD**

Normalize names with collapsed whitespace. `create_goal()` rejects an already
past deadline for an unmet new goal; `update_goal()` permits an existing deadline
to become overdue so the user can update current savings or replace the plan.
Both functions persist the supplied fields, flush, and leave transaction control
to callers. `get_workspace_goal()` filters both ID and workspace ID.

- [ ] **Step 6: Run goal and combined service checks**

Run:

```powershell
uv run pytest tests/planning/test_goals.py tests/planning/test_budgets.py -v
uv run ruff check app/planning tests/planning
uv run ruff format --check app/planning tests/planning
```

Expected: all pass.

- [ ] **Step 7: Commit the goal service slice**

```powershell
git add app/planning tests/planning/test_goals.py
git commit -m "feat: project deterministic savings goals"
```

---

### Task 3: Render explicit budget planning routes

**Files:**
- Create: `app/planning/presentation.py`
- Create: `app/planning/routes.py`
- Create: `app/templates/planning/index.html`
- Modify: `app/main.py`
- Create: `tests/planning/test_routes.py`

**Interfaces:**
- Consumes: Task 1 budget report/save interfaces, Task 2 goal projection listing, existing `require_current_user`, `require_workspace`, `require_csrf`, and `get_db` dependencies.
- Produces: named `planning` GET route and `budget_save` POST route.

- [ ] **Step 1: Write failing authorization, month, and no-write GET tests**

Assert unauthenticated access redirects home, nonmembers receive 404, valid
`?month=2026-08` shows `May 1–July 31, 2026`, and invalid month values return 422
with `Use a valid month in YYYY-MM format.`. Seed a suggestion, GET twice, and
assert zero persisted budgets.

- [ ] **Step 2: Run route tests and confirm 404**

Run: `uv run pytest tests/planning/test_routes.py -v`

Expected: authorized planning route returns 404.

- [ ] **Step 3: Implement presentation helpers and route registration**

`format_money()` delegates the same signed-dollar convention as the dashboard.
`format_month(date)` returns `August 2026`; `format_period(start, end)` returns
`May 1–July 31, 2026`. Parse blank month as `_utc_today()` normalized to day one
and accept only exact `YYYY-MM`. Authorize workspace before report queries.
Register `planning_router` in `create_app()`.

- [ ] **Step 4: Build the planning overview template**

Render the month selector, source-period explanation, one line per suggestion or
accepted budget, monthly history evidence, limit/spent/remaining values, and an
inline form with hidden CSRF/category/month fields. Use button copy `Accept
suggestion` when no budget exists and `Save limit` for an accepted budget. Show
an import-transactions empty state and render goal cards from
`list_goal_projections()` with status badges and calculated values.

- [ ] **Step 5: Add failing budget POST, validation, CSRF, and race tests**

POST `amount=220.01`, assert a 303 redirect and one 22,001-cent row. POST an edit
and assert the same row ID changes amount. Missing CSRF returns 403. Invalid
money/category/month returns 422 without mutation. An inaccessible custom
category returns 404 with no secret category name. Convert a simulated
`IntegrityError` into a rollback and a safe validation response.

- [ ] **Step 6: Implement the explicit budget POST**

Accept strings `category_id`, `period_month`, and `amount`; parse and validate
only after workspace authorization. On `PlanningValidationError`, rollback and
re-render the selected month with retained amount and row-specific errors. On
success, commit and 303 redirect to the selected planning month.

- [ ] **Step 7: Run route/service tests and commit**

```powershell
uv run pytest tests/planning/test_routes.py tests/planning/test_budgets.py -v
uv run ruff check app/planning app/main.py tests/planning
uv run ruff format --check app/planning app/main.py tests/planning
git add app/planning app/templates/planning app/main.py tests/planning/test_routes.py
git commit -m "feat: render monthly budget planning"
```

---

### Task 4: Add goal forms and the travel-goal acceptance flow

**Files:**
- Modify: `app/planning/routes.py`
- Create: `app/templates/planning/goal_form.html`
- Modify: `app/templates/planning/index.html`
- Modify: `app/templates/base.html`
- Modify: `app/templates/workspace_detail.html`
- Modify: `app/static/styles.css`
- Create: `tests/planning/test_acceptance.py`
- Modify: `tests/test_shared_navigation.py`

**Interfaces:**
- Consumes: goal service interfaces from Task 2 and the planning overview from Task 3.
- Produces: GET `/planning/goals/new`, POST `/planning/goals`, GET `/planning/goals/{goal_id}/edit`, and POST `/planning/goals/{goal_id}`.

- [ ] **Step 1: Write failing goal form and travel acceptance tests**

Patch the route clock to August 11, 2026. Submit a travel goal with target
`5000.00`, current `1000.00`, deadline `2026-12-31`, and blank contribution.
Assert 303, persisted contribution remains null, and the planning page shows
`$800.00 per month`, `December 31, 2026`, and `On track`. Submit the inverse
monthly-contribution plan and assert its persisted target date remains null and
the calculated date is December 31, 2026.

- [ ] **Step 2: Add validation, edit, CSRF, and isolation route tests**

Assert both/neither planning inputs, invalid money/date, past deadline, and blank
name return 422 with retained values. Missing CSRF returns 403. Edit current
savings and assert the projection updates. Foreign/missing goal edit GET/POST
return identical generic 404 responses without leaking the foreign name or
amount.

- [ ] **Step 3: Run acceptance tests and confirm missing goal routes**

Run: `uv run pytest tests/planning/test_acceptance.py -v`

Expected: new-goal route returns 404.

- [ ] **Step 4: Implement typed goal form parsing and routes**

Parse target/current/contribution with `parse_money_to_cents()`, parse target
date only from exact `YYYY-MM-DD`, and collect all field errors before rendering.
Create/update with the fixed route `as_of_date`; commit then redirect to
`/planning`. On edit, load the workspace goal before parsing submitted values.
On validation failure, preserve all raw strings and the correct form action.

- [ ] **Step 5: Render accessible goal forms and planning cards**

Provide labels, field-level error text, radio-free guidance that exactly one of
deadline/contribution is required, `min`/`step` attributes for dollar inputs,
and status copy that calls the output a projection. Cards show progress,
remaining, supplied input, calculated missing input, and Edit.

- [ ] **Step 6: Add navigation and responsive planning styles**

Add `Planning` beside Dashboard/Accounts in the signed-in workspace nav and a
Planning panel on the workspace detail page. Add reusable planning grids,
budget rows, goal cards, progress/status treatments, visible focus states, and a
single-column layout under the existing mobile breakpoint without fixed widths
or horizontal overflow.

- [ ] **Step 7: Run planning, navigation, and full feature checks; commit**

```powershell
uv run pytest tests/planning tests/test_shared_navigation.py -v
uv run ruff check app tests/planning tests/test_shared_navigation.py
uv run ruff format --check app tests/planning tests/test_shared_navigation.py
git add app/planning app/templates/planning app/templates/base.html app/templates/workspace_detail.html app/static/styles.css tests/planning tests/test_shared_navigation.py
git commit -m "feat: add savings goal planning flow"
```

---

### Task 5: Document, verify, and review PR8

**Files:**
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Review: all branch changes against the design spec and this plan.

**Interfaces:**
- Consumes: the complete planning feature and existing Alembic chain.
- Produces: beginner documentation, verified branch, final review fixes, and PR-ready evidence.

- [ ] **Step 1: Update user and contributor documentation**

Document opening Planning, selecting a month, interpreting the three-month
median plus 10% buffer, the explicit accept/edit rule, remaining spend, goal
input direction, integer-cent projections, and the absence of automatic money
movement. Add `app/planning/` to the project map and mark PR8 implemented only
after verification succeeds.

- [ ] **Step 2: Run focused and full quality gates fresh**

```powershell
uv run pytest tests/planning tests/test_planning.py tests/test_shared_navigation.py -v
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Record the exact passing test count and duration.

- [ ] **Step 3: Verify migrations and startup**

Against a fresh temporary SQLite database, run Alembic upgrade to head, current,
downgrade to `0008_unique_payslip_income`, and upgrade to head. Assert the head
remains `0008_unique_payslip_income` because PR8 reuses migration 0004. Start
the application lifespan through `TestClient` and assert `/health` returns
`{"status": "ok"}`.

- [ ] **Step 4: Audit privacy, determinism, and scope**

```powershell
git diff --check main...HEAD
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
rg -n "workspace_id|float|langgraph|openai|anthropic|upload|bank" app/planning app/templates/planning tests/planning
rg -n "description|normalized_merchant|storage_path|checksum|SECRET" app/templates/planning
```

Expected: every database-facing planning path has explicit workspace scope; no
floating-point money, raw transaction/private-file fields, AI, bank, upload, or
movement behavior enters the feature.

- [ ] **Step 5: Perform final code review and fix findings test-first**

Invoke `superpowers:requesting-code-review`. Compare every spec requirement to
the diff, add a failing regression test for each valid behavioral finding, apply
the smallest fix, and rerun Steps 2–4.

- [ ] **Step 6: Commit final docs and review fixes**

```powershell
git add README.md docs/where-is-my-money-pr-breakdown.md app tests
git commit -m "docs: explain budgets and savings goals"
```

- [ ] **Step 7: Prepare the pull-request handoff without merging**

Summarize calculation rules, explicit-write behavior, workspace/CSRF isolation,
travel-goal acceptance evidence, exact verification results, unchanged Alembic
head, commits, and remaining out-of-scope work. Do not merge.

## Plan self-review result

- Spec coverage: architecture, budget evidence/calculation/upsert/status,
  savings-goal projection/CRUD/status, security, UI states, navigation, docs,
  migration reuse, acceptance, and release checks each map to a task.
- Placeholder scan: passed; no deferred implementation or vague test step
  remains.
- Type consistency: budget and goal dataclasses and service signatures have one
  definition and the same names in every consumer.
- Scope: PR8b extraction/uploads, AI/LangGraph, bank integration, contribution
  ledgers, deletions, and money movement remain excluded.
