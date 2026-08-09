# PR5 Categorization Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, workspace-isolated manual and rule-based transaction categorization without allowing automatic rules to override manual choices.

**Architecture:** Pure normalization and built-in lookup functions feed a workspace-scoped categorization service. Thin PR3-authorized routes call focused category and transaction services, while PR4's import preview passes normalized candidates through the same categorizer before review and preserves the resulting decision through commit.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2.x, Alembic, SQLite/PostgreSQL-compatible schema, pytest, Ruff.

## Global Constraints

- Start implementation only from merged `main` after both PR3 and PR4 land.
- Do not duplicate PR3 authentication, workspace membership, authorization, or CSRF logic.
- Do not duplicate PR4 CSV parsing, duplicate detection, review state, or transaction commit logic.
- Categorization precedence is exactly: manual transaction choice, workspace merchant rule, built-in merchant rule, built-in `Uncategorized`.
- Saved rules match an exact canonical merchant key; regex, glob, prefix, fuzzy, and learned matching are out of scope.
- Every workspace-owned query includes the active `workspace_id`; browser input is never authoritative for workspace or user identity.
- Owners and accepted members have equal access; pending invitees and non-members have none.
- Statement `description` is immutable; friendly merchant text is stored separately in `normalized_merchant`.
- Manual edit plus optional rule upsert is one database transaction.
- Saving a rule affects later candidates only; no historical bulk recategorization.
- Financial amounts remain signed integer cents.
- UI remains server-rendered FastAPI/Jinja; do not introduce a frontend framework.
- Every implementation slice follows red-green-refactor and ends with focused tests plus a small commit.

---

## Preflight: required merged interfaces

Do this gate after PR4 merges and before Task 1. It intentionally has no implementation commit.

Expected PR3 semantics:

```python
@dataclass(frozen=True)
class WorkspaceContext:
    user_id: int
    workspace_id: int


async def require_current_user(request: Request, session: Session) -> User:
    """Return PR3's authenticated user or raise its standard unauthenticated response."""


async def require_workspace_context(
    workspace_id: int,
    current_user: User,
    session: Session,
) -> WorkspaceContext:
    """Return owner/member context or raise HTTP 404."""


def verify_csrf(request: Request) -> None:
    """Return after verification or raise PR3's standard CSRF response."""
```

Expected PR4 data boundary:

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

- [ ] **Step 1: Create an isolated implementation branch/worktree from merged main**

Use `superpowers:using-git-worktrees` if the environment is not already an isolated worktree.
Suggested branch: `codex/pr-5-categorization-rules`.

- [ ] **Step 2: Verify the base and existing quality gates**

Run:

```powershell
git log -1 --oneline
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Expected: the top commit contains merged PR4, and all checks pass before PR5 changes.

- [ ] **Step 3: Locate the PR3 public dependencies**

Run:

```powershell
rg -n "WorkspaceContext|require_current_user|require_workspace|verify_csrf|csrf" app tests
```

Verify the merged public paths against the `Integration Contract` table below. Do not create a
second membership query if PR3 exposes equivalent semantics under different names; update the plan
to name that equivalent before Task 1.

- [ ] **Step 4: Locate the PR4 candidate, review, and commit boundary**

Run:

```powershell
rg -n "NormalizedTransaction|ReviewedTransaction|preview|review|commit.*transaction|categorization_source" app tests
```

Verify the merged public paths and fields against `Integration Contract`. If PR4 lacks a
single preview categorizer call point, plan the smallest adapter in Task 7; do not move parsing or
duplicate detection into `app/categorization/`.

- [ ] **Step 5: Identify the final PR4 Alembic head and built-in seeding behavior**

Run:

```powershell
uv run alembic heads
rg -n "Uncategorized|Groceries|Entertainment|Category\(" app migrations tests
```

Expected contract: exactly one head, `0006_builtin_categories`. If merged PR4 used a different
revision identifier, update every explicit `0006`/`0007` reference in this plan before Task 1.

### Integration Contract

These concrete public paths are the handoff requested from PR3 and PR4. They do not exist on the
inspected PR2e base. If merged code provides identical semantics under a different path, update the
plan to that exact path during Preflight; semantic differences require the smallest tested adapter.

| Contract | Required semantics | Required public location |
| --- | --- | --- |
| Authorized workspace | owner/accepted member or 404 | `app/workspaces/dependencies.py::require_workspace_context` |
| Workspace value | immutable user/workspace IDs | `app/workspaces/types.py::WorkspaceContext` |
| Current user | authenticated ORM/domain user | `app/auth/dependencies.py::require_current_user` |
| CSRF | validates every state-changing form | `app/core/security.py::verify_csrf` |
| Normalized candidate | row/date/description/cents/fingerprint | `app/imports/types.py::NormalizedTransactionCandidate` |
| Review row | merchant/category/source survive to commit | `app/imports/types.py::ReviewedTransaction` |
| Preview builder | categorizer call after dedupe | `app/imports/service.py::build_import_preview` |
| Commit service | persists authoritative workspace/review fields | `app/imports/service.py::commit_reviewed_transactions` |
| Transaction list | scoped loader/template extension point | `app/transactions/service.py::list_transactions` |
| Alembic parent | seeded built-ins, single head | `0006_builtin_categories` |

---

### Task 1: Enforce category and merchant-rule persistence invariants

**Files:**

- Modify: `app/db/models.py`
- Create: `migrations/versions/0007_categorization_constraints.py`
- Create: `tests/test_categorization_migration.py`
- Modify: `tests/test_imports.py`
- Modify: `tests/conftest.py`

**Interfaces:**

- Consumes: PR4's final Alembic revision and seeded built-in `Category` rows.
- Produces: `Category.name_key: str`, `MerchantRule.updated_at: datetime`, unique same-scope
  categories, and unique `(workspace_id, merchant_pattern)` rules.

- [ ] **Step 1: Add a two-workspace fixture and write failing model constraint tests**

Add tests that prove normalized names/keys cannot duplicate within one scope but can repeat across
workspaces:

```python
def test_custom_category_name_key_is_unique_per_workspace(
    session: Session, workspace: Workspace
) -> None:
    session.add_all(
        [
            Category(workspace_id=workspace.id, name="Trips", name_key="trips", kind="expense"),
            Category(workspace_id=workspace.id, name="TRIPS", name_key="trips", kind="expense"),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_category_name_key_is_allowed_in_different_workspaces(
    session: Session, two_workspaces: tuple[Workspace, Workspace]
) -> None:
    first, second = two_workspaces
    session.add_all(
        [
            Category(workspace_id=first.id, name="Trips", name_key="trips", kind="expense"),
            Category(workspace_id=second.id, name="Trips", name_key="trips", kind="expense"),
        ]
    )
    session.commit()


def test_merchant_key_is_unique_per_workspace(
    session: Session, workspace: Workspace, builtin_category: Category
) -> None:
    session.add_all(
        [
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Netflix",
                category_id=builtin_category.id,
            ),
            MerchantRule(
                workspace_id=workspace.id,
                merchant_pattern="NETFLIX COM",
                normalized_merchant="Netflix 2",
                category_id=builtin_category.id,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run:

```powershell
uv run pytest tests/test_imports.py -k "name_key or merchant_key" -v
```

Expected: FAIL because `name_key` and the new uniqueness constraints do not exist.

- [ ] **Step 3: Add the minimal ORM fields and indexes**

Add `Category.name_key`, `MerchantRule.updated_at`, a custom-category partial unique index, a
built-in partial unique index, and the workspace merchant-key unique constraint. Use both
`sqlite_where` and `postgresql_where` predicates for partial indexes.

```python
Index(
    "uix_custom_category_name_key",
    "workspace_id",
    "name_key",
    unique=True,
    sqlite_where=text("workspace_id IS NOT NULL"),
    postgresql_where=text("workspace_id IS NOT NULL"),
)
```

Define the global built-in index separately on `name_key` where `workspace_id IS NULL`.

- [ ] **Step 4: Write the migration upgrade/downgrade test**

The test must upgrade a temporary SQLite database from the recorded PR4 head to the new revision,
inspect the new columns/indexes, and round-trip downgrade/upgrade. Follow the migration-test helper
pattern PR4 leaves in the repository. Add a pre-upgrade duplicate fixture and assert the migration
raises a message containing `duplicate category name` rather than silently choosing a row.

- [ ] **Step 5: Implement the Alembic migration**

Set `down_revision = "0006_builtin_categories"`. Backfill `name_key` with Python normalization of
existing names before making the column non-null. Check duplicates grouped by workspace scope and
merchant-rule workspace/key before creating unique indexes. Preserve PR4 built-in IDs and existing
transaction foreign keys.

- [ ] **Step 6: Run model and migration tests**

Run:

```powershell
uv run pytest tests/test_imports.py tests/test_categorization_migration.py -v
uv run alembic upgrade head
```

Expected: PASS; one Alembic head remains.

- [ ] **Step 7: Commit the persistence slice**

```powershell
git add app/db/models.py migrations/versions tests/test_imports.py tests/test_categorization_migration.py
git commit -m "feat: enforce categorization data invariants"
```

---

### Task 2: Add pure merchant normalization and built-in rules

**Files:**

- Create: `app/categorization/__init__.py`
- Create: `app/categorization/types.py`
- Create: `app/categorization/normalization.py`
- Create: `app/categorization/builtins.py`
- Create: `tests/categorization/test_normalization.py`
- Create: `tests/categorization/test_builtins.py`

**Interfaces:**

- Consumes: no PR3/PR4 runtime code.
- Produces: `CategorizationSource`, `CategorizationDecision`, `merchant_key()`,
  `merchant_display_fallback()`, and `find_builtin_rule()`.

- [ ] **Step 1: Write failing normalization tests**

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Netflix.com *  ", "NETFLIX COM"),
        ("WHOLE—FOODS   MARKET", "WHOLE FOODS MARKET"),
        ("Café  Déjà Vu", "CAFÉ DÉJÀ VU"),
        ("***", ""),
    ],
)
def test_merchant_key(raw: str, expected: str) -> None:
    assert merchant_key(raw) == expected


def test_display_fallback_collapses_whitespace_and_caps_length() -> None:
    assert merchant_display_fallback("  Corner   Store  ") == "Corner Store"
    assert len(merchant_display_fallback("x" * 300)) == 255
```

- [ ] **Step 2: Run normalization tests and confirm red**

Run: `uv run pytest tests/categorization/test_normalization.py -v`

Expected: FAIL because the package/functions do not exist.

- [ ] **Step 3: Implement the minimal pure functions**

Use `unicodedata.normalize("NFKC", value)`, `str.upper()`, character-wise
`character.isalnum()`, and whitespace collapse. Do not use regular expressions supplied by users.

- [ ] **Step 4: Run normalization tests and confirm green**

Run: `uv run pytest tests/categorization/test_normalization.py -v`

Expected: PASS.

- [ ] **Step 5: Write failing type and built-in lookup tests**

```python
def test_builtin_rule_lookup_is_exact() -> None:
    rule = find_builtin_rule("NETFLIX COM")
    assert rule is not None
    assert rule.normalized_merchant == "Netflix"
    assert rule.category_name == "Entertainment"
    assert find_builtin_rule("NETFLIX COM 1234") is None


def test_categorization_sources_are_stable_strings() -> None:
    assert [source.value for source in CategorizationSource] == [
        "manual",
        "workspace_rule",
        "builtin_rule",
        "uncategorized",
    ]
```

- [ ] **Step 6: Add immutable types and a small built-in catalog**

```python
class CategorizationSource(StrEnum):
    MANUAL = "manual"
    WORKSPACE_RULE = "workspace_rule"
    BUILTIN_RULE = "builtin_rule"
    UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True)
class CategorizationDecision:
    normalized_merchant: str
    category_id: int
    source: CategorizationSource
```

Keep built-in definitions in an immutable tuple/dict keyed by exact canonical keys. Add exactly the
initial design catalog: `NETFLIX COM -> Netflix / Entertainment`,
`SPOTIFY USA -> Spotify / Entertainment`, and `UBER TRIP -> Uber / Transportation`.

- [ ] **Step 7: Run the pure unit tests**

Run: `uv run pytest tests/categorization -v`

Expected: PASS.

- [ ] **Step 8: Commit the pure domain slice**

```powershell
git add app/categorization tests/categorization
git commit -m "feat: add deterministic merchant normalization"
```

---

### Task 3: Implement workspace-scoped categorization precedence

**Files:**

- Create: `app/categorization/service.py`
- Create: `tests/categorization/test_service.py`
- Modify: `tests/conftest.py`

**Interfaces:**

- Consumes: `NormalizedTransactionCandidate` from PR4, models from Task 1, and pure types/functions
  from Task 2.
- Produces: `categorize_candidate(session: Session, workspace_id: int, candidate:
  NormalizedTransactionCandidate) -> CategorizationDecision`.

- [ ] **Step 1: Add explicit two-workspace and built-in-category fixtures**

Create `two_workspaces`, `builtin_categories`, and `candidate_factory` fixtures. Keep all fixture
values synthetic. `builtin_categories` must include `Uncategorized` and `Entertainment` with
`workspace_id=None` and valid `name_key` values.

- [ ] **Step 2: Write the three failing automatic-precedence tests**

```python
def test_workspace_rule_beats_builtin_rule(
    session, workspace, workspace_category, candidate_factory, builtin_categories
):
    # Workspace rule and built-in catalog both match NETFLIX COM.
    session.add(
        MerchantRule(
            workspace_id=workspace.id,
            merchant_pattern="NETFLIX COM",
            normalized_merchant="Streaming",
            category_id=workspace_category.id,
        )
    )
    session.commit()
    decision = categorize_candidate(session, workspace.id, candidate_factory("Netflix.com"))
    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert decision.category_id == workspace_category.id


def test_builtin_rule_beats_uncategorized(
    session, workspace, candidate_factory, builtin_categories
):
    decision = categorize_candidate(session, workspace.id, candidate_factory("Netflix.com"))
    assert decision.source is CategorizationSource.BUILTIN_RULE
    assert decision.category_id == builtin_categories["Entertainment"].id


def test_no_rule_uses_builtin_uncategorized(
    session, workspace, candidate_factory, builtin_categories
):
    decision = categorize_candidate(session, workspace.id, candidate_factory("Unknown Shop"))
    assert decision.source is CategorizationSource.UNCATEGORIZED
    assert decision.category_id == builtin_categories["Uncategorized"].id
```

Manual precedence is proven in Task 5: saving or replacing a rule leaves the edited transaction
`manual` and leaves every earlier stored transaction unchanged. There is deliberately no service
for automatically recategorizing stored transactions.

- [ ] **Step 3: Run the precedence tests and confirm red**

Run: `uv run pytest tests/categorization/test_service.py -k "beats or no_rule" -v`

Expected: FAIL because `service.py` does not exist.

- [ ] **Step 4: Implement the minimal precedence service**

Query `MerchantRule` by both `workspace_id` and exact `merchant_pattern`. Resolve its category only
if built-in or owned by that workspace. Then resolve a built-in catalog entry by category
`name_key`; finally resolve `Uncategorized`. Raise a named `CategorizationConfigurationError` when a
required built-in row is absent.

- [ ] **Step 5: Write failing workspace-isolation tests**

```python
def test_same_key_uses_each_workspaces_own_rule(
    session, two_workspaces, candidate_factory, workspace_categories
):
    first, second = two_workspaces
    first_category, second_category = workspace_categories
    candidate = candidate_factory("Local Shop")
    session.add_all(
        [
            MerchantRule(
                workspace_id=first.id,
                merchant_pattern="LOCAL SHOP",
                normalized_merchant="First Local Shop",
                category_id=first_category.id,
            ),
            MerchantRule(
                workspace_id=second.id,
                merchant_pattern="LOCAL SHOP",
                normalized_merchant="Second Local Shop",
                category_id=second_category.id,
            ),
        ]
    )
    session.commit()
    first_decision = categorize_candidate(session, first.id, candidate)
    second_decision = categorize_candidate(session, second.id, candidate)
    assert first_decision.category_id == first_category.id
    assert second_decision.category_id == second_category.id


def test_rule_cannot_reference_another_workspaces_category(
    session, two_workspaces, candidate_factory, workspace_categories
):
    first, _ = two_workspaces
    _, second_category = workspace_categories
    session.add(
        MerchantRule(
            workspace_id=first.id,
            merchant_pattern="LOCAL SHOP",
            normalized_merchant="Local Shop",
            category_id=second_category.id,
        )
    )
    session.commit()
    decision = categorize_candidate(session, first.id, candidate_factory("Local Shop"))
    assert decision.source is CategorizationSource.UNCATEGORIZED
```

- [ ] **Step 6: Run all categorization service tests**

Run: `uv run pytest tests/categorization/test_service.py -v`

Expected: PASS, including isolation and missing-built-in error tests.

- [ ] **Step 7: Commit the precedence slice**

```powershell
git add app/categorization/service.py tests/categorization/test_service.py tests/conftest.py
git commit -m "feat: apply workspace-scoped categorization precedence"
```

---

### Task 4: Create and list custom workspace categories

**Files:**

- Create: `app/categories/__init__.py`
- Create: `app/categories/service.py`
- Create: `tests/categories/test_service.py`

**Interfaces:**

- Consumes: `Category.name_key` from Task 1.
- Produces:

  ```python
  @dataclass(frozen=True)
  class CategoryChoices:
      workspace: Sequence[Category]
      builtin: Sequence[Category]


  def category_name_key(name: str) -> str:
      """Return a trimmed, NFKC-normalized, whitespace-collapsed casefolded key."""


  def list_accessible_categories(session: Session, workspace_id: int) -> CategoryChoices:
      """Return only global built-ins and this workspace's custom categories."""


  def create_custom_category(session: Session, workspace_id: int, name: str, kind: str) -> Category:
      """Validate, add, flush, and return one workspace-owned category."""
  ```

- [ ] **Step 1: Write failing validation and isolation tests**

Test trimmed creation, allowed kinds (`expense`, `income`, `transfer`), blank/overlength names,
invalid kind, case-insensitive duplicate rejection in one workspace, same name in two workspaces,
and listing that includes built-ins plus only the active workspace's custom categories.

```python
def test_list_accessible_categories_excludes_other_workspace(
    session, two_workspaces, workspace_categories, builtin_uncategorized
):
    first, _ = two_workspaces
    first_custom, second_custom = workspace_categories
    choices = list_accessible_categories(session, first.id)
    assert first_custom in choices.workspace
    assert second_custom not in choices.workspace
    assert builtin_uncategorized in choices.builtin
```

- [ ] **Step 2: Run category service tests and confirm red**

Run: `uv run pytest tests/categories/test_service.py -v`

Expected: FAIL because the category service does not exist.

- [ ] **Step 3: Implement focused service errors and functions**

Define `CategoryValidationError(field: str, message: str)` and
`DuplicateCategoryNameError`. Normalize name keys with trimmed NFKC casefolded text plus whitespace
collapse. Query workspace and built-in branches explicitly; sort each group by case-insensitive
name. Flush but do not commit inside the service so the route owns the request transaction.

- [ ] **Step 4: Run service tests and confirm green**

Run: `uv run pytest tests/categories/test_service.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the category-domain slice**

```powershell
git add app/categories tests/categories
git commit -m "feat: add workspace custom categories"
```

---

### Task 5: Manually recategorize and atomically save future rules

**Files:**

- Create: `app/transactions/service.py` (or extend PR4's focused service module if it already owns
  transaction mutation)
- Create: `tests/transactions/test_categorization_service.py`

**Interfaces:**

- Consumes: Task 2 normalization, Task 4 accessible category rules, and PR4's `Transaction` commit
  model/repository conventions.
- Produces:

  ```python
  @dataclass(frozen=True)
  class ManualCategorizationInput:
      normalized_merchant: str
      category_id: int
      save_for_future: bool


  def manually_categorize_transaction(
      session: Session,
      workspace_id: int,
      transaction_id: int,
      values: ManualCategorizationInput,
  ) -> Transaction:
      """Apply a scoped manual update and optionally upsert one exact-key rule."""
  ```

- [ ] **Step 1: Write failing scoped manual-edit tests**

```python
def test_manual_edit_changes_transaction_but_not_description(
    session, workspace, transaction, workspace_category
):
    original_description = transaction.description
    updated = manually_categorize_transaction(
        session,
        workspace.id,
        transaction.id,
        ManualCategorizationInput("Whole Foods", workspace_category.id, False),
    )
    assert updated.description == original_description
    assert updated.normalized_merchant == "Whole Foods"
    assert updated.category_id == workspace_category.id
    assert updated.categorization_source == "manual"
    assert session.scalar(select(func.count()).select_from(MerchantRule)) == 0


def test_manual_edit_cannot_use_other_workspace_transaction(
    session, two_workspaces, second_transaction, manual_values
):
    first, _ = two_workspaces
    with pytest.raises(TransactionNotFoundError):
        manually_categorize_transaction(session, first.id, second_transaction.id, manual_values)


def test_manual_edit_cannot_use_other_workspace_category(
    session, two_workspaces, first_transaction, second_category
):
    first, _ = two_workspaces
    values = ManualCategorizationInput("Local Shop", second_category.id, False)
    with pytest.raises(CategoryNotAccessibleError):
        manually_categorize_transaction(session, first.id, first_transaction.id, values)
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `uv run pytest tests/transactions/test_categorization_service.py -k "manual_edit" -v`

Expected: FAIL because the mutation service does not exist.

- [ ] **Step 3: Implement minimal scoped manual editing**

Load the transaction by `id AND workspace_id`. Load category by
`id AND (workspace_id = active OR workspace_id IS NULL)`. Validate normalized merchant as trimmed,
nonblank, maximum 255 characters. Set source to `manual`. Do not call `session.commit()` in the
service.

- [ ] **Step 4: Write failing save-for-future tests**

Cover create, same-workspace replacement, cross-workspace isolation, blank derived key rejection,
and a later candidate using the saved rule while the current transaction remains manual.

```python
def test_save_for_future_upserts_rule_and_keeps_current_manual(
    session, workspace, transaction, values_true
):
    updated = manually_categorize_transaction(session, workspace.id, transaction.id, values_true)
    rule = session.scalar(
        select(MerchantRule).where(
            MerchantRule.workspace_id == workspace.id,
            MerchantRule.merchant_pattern == "WHOLE FOODS MARKET",
        )
    )
    assert rule is not None
    assert rule.category_id == values_true.category_id
    assert updated.categorization_source == "manual"


def test_saved_rule_applies_only_to_later_candidate(
    session, workspace, transaction, historical_transaction, values_true, candidate_factory
):
    manually_categorize_transaction(session, workspace.id, transaction.id, values_true)
    decision = categorize_candidate(
        session, workspace.id, candidate_factory(transaction.description)
    )
    assert decision.source is CategorizationSource.WORKSPACE_RULE
    assert historical_transaction.categorization_source == "uncategorized"
```

- [ ] **Step 5: Implement exact-key rule upsert**

Select by `(workspace_id, merchant_pattern)`. Update label/category if present, otherwise add. Leave
an existing rule unchanged when `save_for_future=False`. Let the caller commit transaction and rule
together.

- [ ] **Step 6: Prove rollback is atomic**

Monkeypatch the focused `upsert_workspace_rule()` helper to raise `IntegrityError` after the
transaction object has been changed, invoke the service inside the same request transaction, call
`session.rollback()` in the same exception path the route uses, refresh the `Transaction`, and
assert its old category/source/merchant remain. SQLite does not reliably enforce `VARCHAR` length,
so an overlength string is not a valid failure trigger for this test.

- [ ] **Step 7: Run all transaction categorization service tests**

Run: `uv run pytest tests/transactions/test_categorization_service.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the transaction mutation slice**

```powershell
git add app/transactions tests/transactions
git commit -m "feat: save manual categories and future merchant rules"
```

---

### Task 6: Add authorized category and transaction forms

**Blocked by:** final PR3 dependency/CSRF names and PR4 transaction page layout.

**Files:**

- Create: `app/categories/routes.py`
- Create: `app/categories/forms.py` if PR3/PR4 establish a forms module pattern
- Create: `app/templates/categories/index.html`
- Modify: PR3's application router registration module
- Modify: PR4's transaction list/detail template
- Create: `app/templates/transactions/edit.html`
- Create: `app/transactions/routes.py` or extend PR4's transaction router
- Create: `tests/categories/test_routes.py`
- Create: `tests/transactions/test_categorization_routes.py`

**Interfaces:**

- Consumes: PR3 authorized `WorkspaceContext` and CSRF dependency/token convention; PR4 transaction
  list route/template; Tasks 4 and 5 service APIs.
- Produces:
  `GET/POST /workspaces/{workspace_id}/categories`,
  `GET/POST /workspaces/{workspace_id}/transactions/{transaction_id}/categorization`.

- [ ] **Step 1: Write failing authorization matrix tests**

Parameterize each route for owner, accepted member, pending invitee, non-member, and unauthenticated
user. Owner/member expect 200 (or redirect after valid POST); pending/non-member and cross-workspace
resource IDs expect 404; unauthenticated behavior must match PR3 exactly.

- [ ] **Step 2: Write failing CSRF tests for both POST routes**

Reuse PR3's test helper. Assert missing and invalid tokens are rejected and do not change row counts
or transaction fields.

- [ ] **Step 3: Run route tests and confirm red**

Run:

```powershell
uv run pytest tests/categories/test_routes.py tests/transactions/test_categorization_routes.py -v
```

Expected: FAIL with route-not-found/import errors.

- [ ] **Step 4: Add thin routes using PR3 dependencies**

The URL workspace ID must flow only through the authorized PR3 context. Routes parse form values,
call services, commit once, and map domain validation to field errors. They do not issue membership,
transaction, category, or merchant-rule queries directly.

- [ ] **Step 5: Add custom category UI**

Render separate `Workspace categories` and `Built-in categories` sections. The creation form has
`name`, `kind`, and PR3's CSRF hidden input. Preserve safe submitted values when validation fails.

- [ ] **Step 6: Add manual categorization UI**

Show immutable description, normalized merchant input, grouped category picker, and unchecked
`Use for matching future transactions` checkbox. Add a scoped edit link to PR4's transaction list.
Do not expose raw merchant keys or workspace IDs in editable fields.

- [ ] **Step 7: Add failing validation response tests**

Assert blank merchant, inaccessible category ID, duplicate category name, and blank category name
redisplay errors and do not persist. Assert another workspace's category/transaction behaves as 404,
not as a validation hint.

- [ ] **Step 8: Run route and template tests**

Run:

```powershell
uv run pytest tests/categories/test_routes.py tests/transactions/test_categorization_routes.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit the authorized UI slice**

```powershell
git add app/categories app/transactions app/templates tests/categories/test_routes.py tests/transactions/test_categorization_routes.py
git commit -m "feat: add categorization forms"
```

---

### Task 7: Integrate categorization into PR4 import review

**Blocked by:** final PR4 candidate, preview, review serialization, and commit interfaces.

**Files:**

- Modify: PR4's import preview service
- Modify: PR4's review DTO/schema
- Modify: PR4's import review template
- Modify: PR4's reviewed-transaction commit service
- Modify: PR4's import tests
- Create: `tests/categorization/test_import_integration.py`

**Interfaces:**

- Consumes: actual PR4 equivalents of `NormalizedTransactionCandidate` and
  `ReviewedTransaction`, plus `categorize_candidate()` from Task 3.
- Produces: categorized preview rows whose merchant/category/source survive unchanged to commit,
  except explicit review edits become `manual`.

- [ ] **Step 1: Write a failing built-in preview integration test**

Use PR4's existing synthetic CSV fixture. Include `Netflix.com` and assert the review response/DTO
shows `Netflix`, `Entertainment`, and `builtin_rule` before any transaction is committed.

- [ ] **Step 2: Run the focused integration test and confirm red**

Run: `uv run pytest tests/categorization/test_import_integration.py -k builtin -v`

Expected: FAIL because PR4 preview does not call the categorizer.

- [ ] **Step 3: Add one preview call point after duplicate detection**

For every non-duplicate normalized candidate, call:

```python
decision = categorize_candidate(session, workspace_context.workspace_id, candidate)
```

Copy `decision.normalized_merchant`, `decision.category_id`, and `decision.source.value` into PR4's
review row. If PR4's types use different field names, make a small adapter at this exact boundary.

- [ ] **Step 4: Write the failing workspace-rule-over-built-in test**

Seed a saved `NETFLIX COM` rule in the active workspace and assert preview uses its category/label
with `workspace_rule`. Seed a conflicting rule in another workspace and assert it is ignored.

- [ ] **Step 5: Write the failing review-override commit test**

Submit the reviewed row with a different accessible category. Assert the committed `Transaction`
retains the original description, submitted merchant/category, and source `manual`. Assert no rule
is created unless PR4's review UI explicitly includes and submits the future-rule checkbox; if that
checkbox remains only on the post-import transaction edit form, document that path in the test.

- [ ] **Step 6: Preserve reviewed fields through commit**

Extend only the review state and transaction constructor. Do not recompute rules in commit; a rule
could change between preview and approval, and the user is approving the visible preview.

- [ ] **Step 7: Prove duplicate behavior is unchanged**

Re-run PR4's duplicate re-upload test and add an assertion that a skipped duplicate does not invoke
a persistence side effect or create a `MerchantRule`.

- [ ] **Step 8: Run all PR4 and categorization integration tests**

Run:

```powershell
uv run pytest tests/test_imports.py tests/categorization/test_import_integration.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit the import integration slice**

```powershell
git add app tests
git commit -m "feat: categorize transactions during import review"
```

---

### Task 8: Verify isolation, migration path, documentation, and acceptance

**Files:**

- Create or modify: `tests/test_categorization_acceptance.py`
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Modify: this plan only if merged PR3/PR4 interface paths differ from the concrete contract recorded
  during Preflight

**Interfaces:**

- Consumes: all prior tasks plus PR3/PR4 end-to-end fixtures.
- Produces: executable proof of PR5 acceptance criteria and a beginner-friendly feature explanation.

- [ ] **Step 1: Write the end-to-end saved-rule acceptance test**

Exercise this exact sequence through HTTP/service boundaries established by PR3/PR4:

1. An accepted workspace member opens a transaction.
2. They choose a custom category and normalized merchant.
3. They select `Use for matching future transactions` and submit valid CSRF.
4. The current transaction is `manual`.
5. A later CSV preview with the exact merchant key shows the saved category as `workspace_rule`.
6. The same candidate in another workspace falls through to its own rule/built-in/uncategorized.

- [ ] **Step 2: Run the acceptance test and fix only integration defects**

Run: `uv run pytest tests/test_categorization_acceptance.py -v`

Expected: PASS. Do not broaden matching or add retroactive behavior to make fixtures pass.

- [ ] **Step 3: Verify a clean migration from the pre-PR5 head**

Create a temporary database at the recorded PR4 revision, seed representative built-in categories,
one transaction, and one non-conflicting rule, then upgrade to head. Assert IDs and foreign keys are
preserved. Also verify a brand-new database reaches head.

Run:

```powershell
uv run pytest tests/test_categorization_migration.py -v
uv run alembic heads
```

Expected: PASS and exactly one head.

- [ ] **Step 4: Update beginner-facing documentation**

Update README with:

- how to create a workspace category;
- how a manual correction differs from saving a future rule;
- exact-match limitation;
- precedence in one short list;
- workspace-sharing behavior;
- commands to run the tests.

Mark PR5 complete in the PR breakdown only after all acceptance checks pass. Do not mark PR6 or any
later feature started.

- [ ] **Step 5: Run every quality gate from a clean working tree view**

Run:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
git diff --check
git status --short
```

Expected: all commands pass; status shows only intended PR5 files before the final commit.

- [ ] **Step 6: Commit the acceptance/documentation slice**

```powershell
git add README.md docs tests/test_categorization_acceptance.py
git commit -m "docs: explain categorization rules"
```

- [ ] **Step 7: Review the complete PR5 diff**

Run:

```powershell
git diff --stat main...HEAD
git log --oneline main..HEAD
```

Confirm the diff contains no authentication implementation, CSV parser rewrite, category edit/delete
feature, fuzzy matcher, bulk recategorization, LangGraph/LLM work, or unrelated refactor.

## Final verification matrix

| Requirement | Primary proof |
| --- | --- |
| Manual recategorization | Task 5 service tests; Task 6 route tests |
| Custom workspace categories | Task 4 service tests; Task 6 route tests |
| Merchant normalization | Task 2 unit tests |
| Save for future | Task 5 upsert/later-candidate tests |
| Manual precedence | Task 3 preservation test; Task 7 review override |
| Workspace over built-in | Task 3 precedence tests; Task 7 preview test |
| Built-in over Uncategorized | Task 3 precedence tests |
| Workspace isolation | Tasks 3–7 two-workspace tests |
| PR3 consumption | Preflight + Task 6 authorization/CSRF matrix |
| PR4 consumption | Preflight + Task 7 integration tests |
| Duplicate safety | Task 7 PR4 regression test |
| Migration safety | Tasks 1 and 8 migration tests |

## Execution handoff after PR4 lands

1. Create `codex/pr-5-categorization-rules` from merged `main`; do not reuse this detached planning
   checkout as the production branch.
2. Complete Preflight and verify every Integration Contract path against merged code. Update the
   plan to exact equivalent paths before writing code if names differ.
3. If PR3/PR4 semantics match under different names, adapt imports only. If semantics are missing,
   add the smallest public adapter and cover it with a regression test.
4. Execute Tasks 1–8 in order. Tasks 2 and the pure parts of Task 4 are conceptually independent,
   but keeping task order avoids migration/type drift for a beginner contributor.
5. Request code review after the full suite is green. Create a production PR only then, with PR5's
   acceptance matrix in the description.

The implementation is currently blocked at Preflight because the inspected merged base contains
PR2e but not PR3 or PR4. This plan deliberately provides no production-code shortcut around that
blocker.
