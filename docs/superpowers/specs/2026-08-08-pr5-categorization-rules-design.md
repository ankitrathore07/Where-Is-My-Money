# PR5 Categorization Rules Design

**Status:** Design ready; implementation intentionally waits for PR3 and PR4 integration contracts.

**Base inspected:** merged `main` at `a12aa57`, including PR2e.

## Purpose

PR5 adds understandable, deterministic categorization behavior on top of the authenticated
workspace boundary from PR3 and the reviewed CSV transaction flow from PR4. A workspace member
can correct one transaction, optionally save that correction for matching future imports, create
workspace-only categories, and see a human-readable normalized merchant name.

The defining invariant is:

> A manual choice on a transaction always wins. Otherwise use a matching rule from the active
> workspace, then a built-in rule, then the built-in `Uncategorized` category.

PR5 does not import CSV files, authenticate users, choose the active workspace, or provide broad
pattern matching. It consumes those boundaries from PR3 and PR4.

## Current foundation

PR2b already provides `Category`, `MerchantRule`, and `Transaction` models. `Category.workspace_id`
is nullable so global built-ins and workspace-owned custom categories can share the table.
`MerchantRule.workspace_id` is required. `Transaction` already has `description`,
`normalized_merchant`, `category_id`, and `categorization_source` fields, plus workspace/date,
category, and normalized-merchant indexes.

The current foundation deliberately has no feature services or routes. PR3 and PR4 are not present
on `main`, so their final module names and signatures cannot be verified yet. The contracts below
are therefore merge requirements for those PRs and a rebase checklist for PR5, not claims about
code that already exists.

## Design choices considered

### Recommended: exact canonical merchant keys

Convert a statement description into a stable comparison key by Unicode normalization, uppercase,
punctuation-to-space conversion, and whitespace collapse. A saved rule matches that key exactly.
For example, `"  Netflix.com *  "` becomes `"NETFLIX COM"`.

This is predictable, safe for a beginner-oriented finance app, portable across SQLite and
PostgreSQL, and cannot accidentally categorize a large set of unrelated merchants. Its trade-off
is that descriptions containing changing store numbers or authorization IDs may need more than one
rule.

### Rejected for V1: user-authored glob or regular-expression rules

Patterns could match more statement variants, but escaping, ordering, catastrophic regex behavior,
and surprising broad matches make the feature harder to teach and trust. The existing database
column remains named `merchant_pattern` for migration compatibility, but PR5 treats its value as an
exact canonical key.

### Rejected for V1: learned or fuzzy merchant matching

Similarity scoring would be convenient but introduces thresholds and non-obvious decisions. It
also makes precedence tests probabilistic. It can be added later behind the same categorization
service if real statement fixtures demonstrate the need.

## Product behavior

The expanded built-in taxonomy, initial merchant catalog, independent Subscription label, ambiguity
policy, and catalog-maintenance rules are defined in
`docs/superpowers/specs/2026-08-09-pr5-built-in-categorization-catalog-design.md`. That document
extends this design and is part of the same PR5 scope.

### Built-in categories

PR4 owns creation of the initial built-in category rows because its import review and transaction
list need them before PR5 lands. The required names, kinds, boundaries, and initial catalog are
defined by the expanded catalog design. In particular, it supersedes the earlier short category
list with the complete V1 taxonomy, including `Dining & Drinks`, `Software & Online Services`, and
`Health & Fitness`.

Built-ins have `workspace_id = NULL`. Their names are stable application identifiers in V1; the UI
may display them directly. PR5 must fail clearly at startup/test setup if `Uncategorized` is absent,
rather than silently storing a null category.

### Custom workspace categories

An authorized workspace member can list and create custom categories for the active workspace.
Names are trimmed, one to 100 characters, and compared case-insensitively within the workspace.
Kinds are limited to `expense`, `income`, and `transfer`.

A custom category may reuse a built-in name because it is owned by a different scope, but the
category picker displays custom entries under `Workspace categories` and built-ins under
`Built-in categories` so that choice is explicit. Two custom categories in the same workspace may
not have names that differ only by case or surrounding whitespace.

Editing, deleting, merging, reordering, colors, icons, and per-user categories are outside PR5.
Creation plus listing is sufficient for the first end-to-end correction workflow.

### Merchant normalization

PR5 distinguishes three values:

- `description`: the original statement text stored by PR4 and never rewritten by PR5.
- merchant key: an internal comparison value produced from `description`; it is stored in
  `MerchantRule.merchant_pattern`, not displayed as the friendly merchant name.
- `normalized_merchant`: a user-facing label such as `Whole Foods` or `Netflix` stored on the
  transaction.

The canonical key algorithm is pure and deterministic:

1. Apply Unicode NFKC normalization.
2. Trim leading and trailing whitespace.
3. Convert to uppercase.
4. Replace every run of characters other than Unicode letters or digits with one space.
5. Collapse whitespace runs to one ASCII space.

An empty key is invalid for saving a future rule. It may still be categorized as `Uncategorized`
during import.

When no merchant rule matches, the display fallback is the statement description with whitespace
collapsed and surrounding whitespace removed, truncated to 255 characters. The fallback is not a
stored rule.

### Built-in merchant rules

Built-in merchant rules live in an immutable Python catalog, not the database. Each entry maps
one or more exact canonical keys to a normalized merchant label and a built-in category name.
Keeping them in code makes review and precedence explicit and avoids pretending global rows are
workspace-owned `MerchantRule` records.

The initial fixture-backed merchant keys, categories, amount directions, Subscription values, and
intentional exclusions are defined in the expanded catalog design. A missing or ambiguous match
correctly falls through to `Uncategorized`.

### Categorization precedence

The categorizer returns a `CategorizationDecision` containing `normalized_merchant`, `category_id`,
`is_subscription`, and one of four source values:

- `manual`: a member explicitly chose the transaction category. This value is never replaced by an
  automatic pass.
- `workspace_rule`: the active workspace has an exact rule for the key.
- `builtin_rule`: the immutable built-in catalog matched and its built-in category exists.
- `uncategorized`: no rule matched; the built-in `Uncategorized` row is selected.

Automatic evaluation order is workspace rule, built-in rule, then `Uncategorized`. Manual is not a
rule lookup: stored transactions with source `manual` are never fed back through candidate
categorization. PR5 intentionally has no bulk automatic-recategorization path.

Rules never cross workspace boundaries. A workspace rule may reference only a built-in category or
a custom category owned by that same workspace.

### Manual recategorization

From the transaction list/review UI, a member can open an edit form for a transaction in the active
workspace. The form shows the immutable statement description, editable normalized merchant label,
a category picker, an independent Subscription checkbox, and `Use for matching future
transactions` checkbox.

Submitting the form always updates the selected transaction and sets
`categorization_source = "manual"`. The service rejects a transaction from another workspace and a
category that is neither built-in nor owned by the active workspace. The route behaves as not found
for cross-workspace IDs so it does not reveal whether the record exists.

The transaction update and optional rule save occur in one database transaction. If either fails,
neither is committed.

### Save for matching future transactions

If the checkbox is selected, PR5 derives the exact merchant key from the transaction's original
description and upserts one `MerchantRule` for `(workspace_id, merchant_pattern)`. The rule stores
the submitted normalized merchant label, category, and Subscription choice.

If that workspace already has a rule for the key, the new explicit choice replaces its label and
category and Subscription choice. A rule in another workspace is untouched. Saving a rule does not
recategorize historical transactions; it affects candidates evaluated after the save. The
transaction being edited stays `manual`, even though its values now equal the rule.

Unchecking the box means “do not create or update a rule,” not “delete an existing rule.” Rule
management and deletion are deferred.

### Import review behavior

PR4 first parses and normalizes structural CSV values: date, original description, signed integer
cents, and duplicate fingerprint. PR5 then categorizes each non-duplicate candidate before the
review page is rendered. The review page shows the suggested merchant, category, Subscription
value, and source.

If the member changes the suggestion during review, PR4 commits that row as `manual`; otherwise it
commits the source returned by PR5. A candidate must not be saved before review approval. Rebuilding
a preview may reevaluate current rules because no transaction has been committed yet.

## Architecture and boundaries

PR5 follows the modular-monolith shape already chosen by the project:

- `app/categorization/types.py`: source enum and immutable decision value.
- `app/categorization/normalization.py`: pure merchant-key and display-fallback functions.
- `app/categorization/builtins.py`: immutable built-in merchant definitions.
- `app/categorization/service.py`: scoped lookups and precedence evaluation.
- `app/categories/service.py`: accessible-category listing and custom-category creation.
- `app/transactions/service.py`: scoped manual recategorization and atomic rule upsert.
- `app/categories/routes.py` and `app/transactions/routes.py`: thin authenticated HTTP adapters.
- focused Jinja templates for category listing/creation and transaction edit fields.

Routes never build workspace-scoped queries directly. They receive an authorized workspace context
from PR3 and pass its ID to services. Services still include `workspace_id` in every read/write
query; authorization at the route is not treated as a substitute for data isolation.

## Required PR3 auth/workspace interface

PR5 requires PR3 to expose these semantics. Names may be adapted once PR3 lands, but there must be
one obvious equivalent for each contract:

```python
@dataclass(frozen=True)
class WorkspaceContext:
    user_id: int
    workspace_id: int


async def require_current_user(request: Request, session: Session) -> User:
    """Return the signed-in user or raise the PR3 unauthenticated response."""


async def require_workspace_context(
    workspace_id: int,
    current_user: User,
    session: Session,
) -> WorkspaceContext:
    """Return context only for an owner/accepted member; otherwise raise 404."""


def verify_csrf(request: Request) -> None:
    """Reject a state-changing request without PR3's valid signed CSRF token."""
```

Consumption rules:

1. PR5 routes are nested under `/workspaces/{workspace_id}/categories` and
   `/workspaces/{workspace_id}/transactions/{transaction_id}/categorization` and depend on the PR3
   context.
2. No form field or query parameter supplies the authoritative workspace or user ID.
3. Owners and accepted household members have equal PR5 permissions, matching the roadmap.
4. Pending invitations do not grant access.
5. Unauthorized workspace/resource IDs return 404; unauthenticated requests use PR3's standard
   redirect or 401 behavior consistently.
6. Every POST uses PR3's CSRF dependency and token rendering convention.

If PR3 uses a dependency class or an `Annotated` alias instead of the functions above, PR5 should
consume that public alias rather than duplicate membership queries.

## Required PR4 transaction/import interface

PR5 requires PR4 to retain a stable pure-data boundary after CSV value normalization and before
review rendering:

```python
@dataclass(frozen=True)
class NormalizedTransactionCandidate:
    row_number: int
    date: datetime
    description: str
    amount_cents: int
    duplicate_fingerprint: str


@dataclass(frozen=True)
class ReviewedTransaction:
    candidate: NormalizedTransactionCandidate
    normalized_merchant: str
    category_id: int
    categorization_source: str
```

PR4 must provide these behaviors to PR5:

1. `description` is the original normalized-text statement description and is available before a
   `Transaction` row exists.
2. Duplicate candidates are removed or marked before PR5 categorization; PR5 does not own duplicate
   detection.
3. The preview builder has one injection/call point for
   `categorize_candidate(session: Session, workspace_id: int, candidate:
   NormalizedTransactionCandidate) -> CategorizationDecision`.

4. Review state carries `normalized_merchant`, `category_id`, and `categorization_source` without
   recomputing them during commit.
5. A review edit changes the row source to `manual`.
6. The commit service constructs `Transaction` with the reviewed fields and the authoritative
   workspace/import job IDs; it never accepts workspace ID from browser row data.
7. PR4 exposes or centralizes transaction list loading so PR5 can add an edit link without creating
   an unscoped second list query.

If PR4 chooses Pydantic models instead of dataclasses, the field names and meanings above remain the
compatibility contract. PR5 should adapt at its boundary rather than force a rewrite of PR4.

## Persistence changes

PR5 adds one Alembic migration after the final PR4 head:

1. Add a unique constraint/index for one exact merchant rule per workspace:
   `(workspace_id, merchant_pattern)`.
2. Add `updated_at` to `merchant_rules` so rule replacement is auditable.
3. Add `name_key` to `categories`, backfill it from trimmed/casefolded names, and make it non-null.
4. Add a partial unique index on `(workspace_id, name_key)` for custom rows where
   `workspace_id IS NOT NULL`.
5. Add a partial unique index on `name_key` for built-in rows where `workspace_id IS NULL`.
6. Add non-null `is_subscription` boolean columns with a false default to `transactions` and
   `merchant_rules`.

The migration must first detect duplicate existing values and fail with a useful message in tests,
rather than silently dropping a rule or category. SQLite and PostgreSQL versions of the partial
index predicates must be declared explicitly.

`merchant_pattern` is not renamed in PR5 to keep the migration small. Code and documentation call
it an exact merchant key and never expose it as a wildcard expression.

## Validation and failure behavior

- Blank or over-100-character category names return the existing form with a field error.
- Unsupported category kinds return a field error.
- Duplicate custom names return a field error even if the database constraint detects a race.
- Blank or over-255-character normalized merchant labels return a field error.
- An empty canonical merchant key prevents future-rule saving but does not prevent manual category
  correction.
- A missing built-in category is a configuration/data error surfaced during tests or import
  preview, not silently replaced with null.
- Cross-workspace transaction, category, or rule access is reported as not found.
- Database exceptions roll back both the transaction update and rule upsert and use the app's
  standard error response; sensitive SQL values are not logged.

## Workspace isolation invariants

These rules hold at the database-service boundary, even when a route was already authorized:

- Transaction reads use both `Transaction.id` and `Transaction.workspace_id`.
- Custom category reads use the active workspace ID; built-ins are included only with an explicit
  `workspace_id IS NULL` branch.
- Merchant rule reads and upserts use both workspace ID and merchant key.
- Category and Subscription values are taken from the same winning rule; another workspace's rule
  cannot supply either value.
- A category selected for a transaction must satisfy
  `category.workspace_id IS NULL OR category.workspace_id = active_workspace_id`.
- The categorizer accepts workspace ID as an explicit required argument and has no global “current
  workspace” state.
- Tests use two workspaces with the same merchant key and intentionally different categories.

## Test strategy

### Pure unit tests

- Unicode/case/punctuation/whitespace merchant-key normalization.
- Display fallback trimming, whitespace collapse, and length limit.
- Built-in catalog lookup and no-match behavior.
- Exact matching: similar prefixes do not match.
- Catalog validation for category names, unique canonical keys, amount directions, and Subscription
  values.

### Service tests with an in-memory database

- Precedence: existing manual transaction remains manual; workspace rule beats built-in; built-in
  beats `Uncategorized`; missing matches select `Uncategorized`.
- Rule isolation: identical keys in two workspaces yield their own decisions.
- Category access: built-in and same-workspace custom categories are accepted; another workspace's
  category is rejected.
- Custom category case-insensitive uniqueness is scoped to a workspace.
- Manual edit without checkbox changes only the transaction.
- Manual edit with checkbox atomically updates the transaction and creates/replaces only the active
  workspace's rule.
- Manual and workspace Subscription choices override built-in values without changing category
  precedence.
- Saved rule affects a later candidate but does not rewrite prior transactions.
- Forced rule failure rolls back the manual transaction update.

### Route tests

- Unauthenticated behavior follows PR3.
- Owner and accepted member can list/create categories and edit transactions.
- Pending/non-member access and cross-workspace IDs return 404.
- POSTs reject missing/invalid CSRF.
- Validation redisplays safe form values and does not persist changes.

### PR4 integration tests

- A sample normalized candidate receives a built-in category and Subscription suggestion on the
  review page.
- A workspace rule overrides that suggestion.
- A review correction commits with source `manual`.
- Saving a rule, then previewing a later import, applies `workspace_rule`.
- Duplicate re-upload behavior remains unchanged and does not create extra rules.

### Migration and full-suite checks

- Upgrade from the final PR4 migration head preserves built-ins and existing transactions.
- Unique indexes reject same-scope duplicates and permit the same names/keys in different
  workspaces.
- Alembic upgrade from an empty database reaches head.
- Ruff, formatting, and the entire pytest suite pass.

## Out of scope

- Regex, glob, fuzzy, prefix, or learned merchant matching.
- Retroactive bulk recategorization when a rule changes.
- Category edit/delete/merge, colors, icons, budgets, and reporting.
- Rule list/edit/delete UI and per-rule priority.
- Per-user rules inside a shared workspace.
- Multi-currency behavior.
- Import parsing, mapping, duplicate detection, file retention, authentication, membership, or CSRF
  implementation (owned by PR3/PR4).

## Blocked and independent work

### Safe before PR3/PR4 land

- Review and approve this design and implementation plan.
- Refine pure normalization examples against anonymized statement descriptions.
- Agree on the PR3 `WorkspaceContext`/CSRF public dependency and PR4 candidate/review DTO fields.
- Prepare test fixture CSVs containing no real financial data.

### Blocked by PR3

- PR5 routes, route tests, active-workspace navigation, 404 behavior, and CSRF wiring.
- Final names/import paths for current-user and authorized-workspace dependencies.

### Blocked by PR4

- Final migration revision ID and safe category backfill (PR4 owns built-in seeding).
- Import preview categorization, review overrides, commit integration, and end-to-end saved-rule test.
- Transaction list edit links and final template composition.
- Exact DTO/repository import paths.

### Must wait for both

- Full authorization plus import acceptance tests and the final quality-gate run.
- Production implementation branch/PR. Start it only from merged `main` after PR3 and PR4.

## Acceptance criteria

PR5 is complete when all of the following are true:

1. A member can create and select a workspace-only category.
2. A member can manually change a transaction's category and normalized merchant.
3. A member can independently set or clear Subscription without replacing the primary category.
4. The manual transaction stores source `manual` and automatic evaluation cannot overwrite it.
5. With `Use for matching future transactions` selected, a later exact-key candidate in the same
   workspace receives the saved merchant/category with source `workspace_rule`.
6. The same key in another workspace does not see that rule.
7. Without a workspace rule, a built-in rule applies; without either, the built-in
   `Uncategorized` category applies.
8. Cross-workspace transaction/category IDs and unauthorized routes do not disclose or mutate data.
9. Import review still precedes commit and duplicate re-upload remains safe.
10. Migration-from-PR4, full pytest, Ruff lint, and Ruff format checks pass.

## PR4-to-PR5 execution handoff

After PR4 merges, the PR5 implementer should rebase/create a fresh branch from merged `main`, record
the actual PR3/PR4 contract locations in the implementation plan, and run the existing suite before
writing a failing PR5 test. If an expected contract is missing, add only the smallest adapter or
PR4 refactor needed to expose it; do not duplicate authentication, workspace membership, import
parsing, duplicate detection, or transaction commit logic inside PR5.
