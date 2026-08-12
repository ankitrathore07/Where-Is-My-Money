# PR 8 — Budgets and Savings Goals Design

## Outcome

Add a workspace planning center where a member can review explainable monthly
budget suggestions, explicitly accept or edit a category limit, see remaining
spend for a selected month, and create or update savings goals whose missing
deadline or monthly contribution is calculated deterministically.

This PR uses the planning tables delivered in migration
`0004_planning_insights`. It does not change the schema because the existing
`budgets` and `savings_goals` columns preserve the accepted budget and the one
user-supplied goal-planning input without storing derived values.

## Scope

In scope:

- a server-rendered planning center at
  `/workspaces/{workspace_id}/planning`;
- monthly category-limit suggestions and explicit accept/edit actions;
- accepted-budget spending and remaining views for a selected calendar month;
- savings-goal creation and editing;
- missing deadline or monthly-contribution projections;
- completed, on-track, and off-track status;
- workspace authorization, CSRF protection, validation, navigation, and tests;
- beginner-facing documentation for the new feature.

Out of scope:

- account-statement extraction and generic upload changes from PR 8b;
- LangGraph, LLMs, or other AI behavior;
- bank connections, transfers, payments, or any money movement;
- contribution ledgers, recurring transfer scheduling, or automatic budget
  creation;
- deleting budgets or goals.

## Architecture

Create a focused `app/planning/` package:

- `types.py` contains immutable input and report dataclasses.
- `service.py` owns validation, integer-cent calculations, workspace-scoped
  queries, budget upserts, goal persistence, and projections.
- `presentation.py` owns money and month labels needed only by the planning UI.
- `routes.py` authorizes the workspace before delegating to the service and
  renders server-side Jinja pages.

Templates live under `app/templates/planning/`. The planning overview combines
the selected month's budget suggestions/statuses and all savings-goal
projections. A separate goal form handles create and edit so validation errors
retain submitted values and the correct action.

Register the router in `app/main.py`. Add Planning links to the signed-in
workspace navigation and workspace detail page. The feature follows PR7's
modular-monolith, server-rendered, explicit-date, and deterministic-service
conventions.

## Budget Calculations

The selected budget month is always normalized to its first day. The route
accepts only `YYYY-MM`; blank input defaults to the current UTC month. Invalid
input returns the planning shell with status 422 and a clear format message.

For a selected month, the suggestion window is the three immediately preceding
complete calendar months. For example, an August 2026 budget uses May 1 through
July 31, 2026. For each eligible category:

1. Include negative transactions assigned to an accessible expense category.
2. Convert each negative amount to positive spending cents.
3. Sum spending separately for each of the three months. A month without
   spending contributes zero.
4. Take the middle of the three integer monthly totals.
5. Add a 10% buffer and round half up to the nearest cent using integer
   arithmetic.

Only categories with positive spending somewhere in the source window produce
a suggestion. Income, transfer, unassigned, inaccessible, and the built-in
`Uncategorized` category do not produce suggestions. Each suggestion includes
the three monthly totals and exact source dates so the result is explainable.

The GET never writes. A member must submit an explicit CSRF-protected form to
accept the displayed amount or replace it with another positive dollar amount.
The service validates that the category is a built-in or active-workspace
expense category and upserts the unique `(workspace, category, month)` budget.

For accepted budgets, selected-month spending uses the same assigned expense
transaction rules. `remaining_cents = limit_cents - spent_cents`; it remains
negative when overspent. Accepted budgets remain visible even when the source
history has no current suggestion.

## Savings-Goal Calculations

A goal contains a normalized nonblank name, positive target cents,
nonnegative current savings cents, and exactly one user-supplied planning input:

- a target date; or
- a positive monthly contribution in cents.

The service persists the supplied input and leaves the calculated column null,
which keeps the calculation direction explicit and lets every projection be
recomputed for an `as_of_date`. Routes use the current UTC date; tests pass a
fixed date.

`remaining_cents = max(target_cents - current_cents, 0)`.

When a target date is supplied, contribution months are the inclusive calendar
months from the as-of month through the target-date month. A future date in the
same month therefore has one contribution month. The required contribution is
`ceil(remaining_cents / contribution_months)`, implemented with integer cents.
An unmet goal whose date is before the as-of date is off track and has no
achievable required contribution for that expired plan.

When a monthly contribution is supplied, required months are
`ceil(remaining_cents / monthly_contribution_cents)`. The calculated target date
is the last day of the final contribution month, counting the as-of month as
month one. A completed goal uses the as-of date as its projection date and zero
remaining cents.

Status values are:

- `completed` when current savings meet or exceed the target;
- `off_track` when an unmet deadline has passed;
- `on_track` for a valid active projection that reaches the target under the
  displayed required contribution or calculated deadline.

The UI describes these as projections, not guarantees, and never initiates a
transfer.

## Validation and Security

All reads and writes receive the authorized workspace ID from
`require_workspace`; no service accepts a workspace identifier from form data.
Goal lookup and budget category lookup filter by workspace before validation so
foreign IDs return the same generic 404 as missing IDs. Built-in categories are
accessible globally, while custom categories are accessible only to their
owning workspace.

All mutations require the existing CSRF dependency. Dollar parsing uses
`Decimal`, accepts at most two fractional digits, and converts once to integer
cents. Limits reject zero/negative values. Goal names are at most 255
characters. Current savings may exceed the target so completed goals can retain
truthful balances.

The planning report exposes only aggregate category totals and the active
workspace's goal fields. It never exposes transaction descriptions, merchants,
foreign-workspace category names, file metadata, or account data.

## UI and Empty/Error States

The planning overview contains:

- a `YYYY-MM` month selector;
- a budget section with source-period explanation;
- accepted limit, spent, and remaining values;
- explicit Accept suggestion or Save edited limit forms;
- a no-history state that links to transaction import;
- goal cards showing progress, remaining amount, supplied planning input,
  calculated missing value, status, and an Edit link;
- a New savings goal action and a no-goals explanation.

Validation responses use status 422, keep the authorized planning context, and
preserve submitted form values. Successful POSTs use 303 redirects to prevent
duplicate submissions.

## Testing

Service tests cover:

- three-month median and 10% half-up cent rounding;
- zero-spend months, month/year boundaries, excluded categories, and source
  period evidence;
- explicit budget upsert, current-month spending, negative remaining values,
  and accessible-category enforcement;
- target-date contribution ceiling calculations;
- monthly-contribution target-date calculations across year boundaries;
- completed and overdue goal statuses, validation, edit behavior, and
  deterministic repeated results;
- cross-workspace isolation for budgets, transactions, categories, and goals.

Route and acceptance tests cover authentication, workspace authorization,
CSRF, invalid month/date/money input, retained form values, explicit acceptance
without GET-side writes, navigation, and a fixed travel-goal scenario that
calculates the expected contribution or target date in both HTML and persisted
state.

Release verification runs focused planning tests, the full test suite, Ruff
lint/format checks, migration upgrade/downgrade/upgrade checks, startup health,
and a final privacy/scope diff audit.
