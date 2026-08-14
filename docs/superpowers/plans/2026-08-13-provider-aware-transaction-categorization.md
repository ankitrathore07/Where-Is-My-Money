# Provider-Aware Transaction Categorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an account-linked, provider-aware transaction import with deterministic Chase categorization and an optional privacy-bounded LangGraph/OpenAI fallback.

**Architecture:** Extend the current account and import-job models rather than creating a second account relationship. Resolve a provider profile from the selected account, apply workspace/provider/built-in categorization in deterministic order, and invoke a dependency-injected LangGraph classifier only for the remaining sanitized descriptions. Keep the generic mapping path and make every AI failure resolve to Uncategorized.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic Settings, LangGraph, OpenAI Python SDK, Jinja2, browser JavaScript, pytest, Ruff.

## Global Constraints

- Work only in `C:\repo\Where-Is-My-Money\.worktrees\provider-aware-transaction-imports` on branch `codex/provider-aware-transaction-imports`.
- A transaction has exactly one primary category and an independent `is_subscription` boolean.
- Never send amount, date, balance, filename, account/user/workspace identity, or source bytes to OpenAI.
- AI categorization defaults off and fails closed to Uncategorized.
- Only synthetic financial data may be committed.
- Preserve the legacy generic import routes and the temporary `transaction_statement` category alias.
- BEST BUY AUTO PYMT is a Transfers provider rule.
- Do not add unconfirmed Microsoft, Xoom, or remote-deposit deterministic rules.

---

### Task 1: Institution identity on accounts

**Files:**
- Create: `app/institutions/__init__.py`
- Create: `app/institutions/catalog.py`
- Create: `migrations/versions/0010_provider_aware_transaction_imports.py`
- Modify: `app/db/models.py`
- Modify: `app/accounts/types.py`
- Modify: `app/accounts/service.py`
- Modify: `app/accounts/routes.py`
- Modify: `app/templates/accounts/form.html`
- Test: `tests/institutions/test_catalog.py`
- Test: `tests/accounts/test_service.py`
- Test: `tests/accounts/test_routes.py`
- Test: `tests/test_provider_aware_migration.py`

**Interfaces:**
- Produces: `INSTITUTIONS`, `get_institution(key)`, `institution_options()`, and `Account.institution_key`.
- Produces: `AccountInput(name, account_type, institution_key, institution, is_liability)`.
- Consumes: existing account type values and workspace-scoped account service.

- [ ] **Step 1: Write failing institution and account tests**

```python
def test_institution_catalog_has_stable_known_keys() -> None:
    assert tuple(item.key for item in INSTITUTIONS) == (
        "chase",
        "bank_of_america",
        "citi",
        "capital_one",
        "american_express",
        "discover",
        "wells_fargo",
        "other",
    )


def test_create_account_persists_catalog_identity(session, workspace) -> None:
    account = create_account(
        session,
        workspace.id,
        AccountInput("Everyday", "checking", "chase", "", False),
    )
    assert account.institution_key == "chase"
    assert account.institution == "Chase"
```

Add route tests showing the form contains a known-institution select plus an Other text field, and
that an unknown institution key returns a field error without mutating the account.

- [ ] **Step 2: Run the new tests and verify missing catalog/input support fails**

Run:

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\institutions tests\accounts\test_service.py tests\accounts\test_routes.py -q
```

Expected: collection or assertion failures because the catalog and `institution_key` do not exist.

- [ ] **Step 3: Implement the immutable catalog and account validation**

```python
@dataclass(frozen=True)
class InstitutionDefinition:
    key: str
    label: str


INSTITUTIONS = (
    InstitutionDefinition("chase", "Chase"),
    InstitutionDefinition("bank_of_america", "Bank of America"),
    InstitutionDefinition("citi", "Citi"),
    InstitutionDefinition("capital_one", "Capital One"),
    InstitutionDefinition("american_express", "American Express"),
    InstitutionDefinition("discover", "Discover"),
    InstitutionDefinition("wells_fargo", "Wells Fargo"),
    InstitutionDefinition("other", "Other / manual mapping"),
)
```

Normalize known institutions to their catalog label. Require free-form `institution` only when the
key is `other`; preserve a blank nullable key for legacy records loaded from the database.

- [ ] **Step 4: Add and exercise the migration**

Add nullable `accounts.institution_key`, a catalog-key check constraint, and update the ORM. Extend
the existing categorization-source check constraint, if present in the current database lineage, to
allow `provider_rule` and `ai_suggestion`. Test upgrade from `0009`, inserts for null and valid keys,
rejection of invalid keys, and downgrade back to `0009`.

Run:

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\test_provider_aware_migration.py -q
```

Expected: all migration cases pass.

- [ ] **Step 5: Render and process the account form**

Pass `institution_options` to both create and edit rendering. The form submits
`institution_key`; JavaScript-free rendering keeps the Other institution text input visible and the
service ignores it for known keys.

- [ ] **Step 6: Run task tests and commit**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\institutions tests\accounts tests\test_provider_aware_migration.py -q
git add app/institutions app/accounts app/templates/accounts/form.html app/db/models.py migrations/versions/0010_provider_aware_transaction_imports.py tests/institutions tests/accounts tests/test_provider_aware_migration.py
git commit -m "feat: add stable institution identities"
```

Expected: task tests pass and the commit contains only institution/account changes.

### Task 2: Account-linked document choices and import jobs

**Files:**
- Modify: `app/documents/catalog.py`
- Modify: `app/documents/routes.py`
- Modify: `app/templates/documents/upload.html`
- Modify: `app/static/document-upload.js`
- Modify: `app/imports/service.py`
- Test: `tests/documents/test_catalog.py`
- Test: `tests/documents/test_routes.py`
- Test: `tests/documents/test_upload_queue_browser.py`
- Test: `tests/test_import_service.py`

**Interfaces:**
- Produces: `compatible_account_types(category_key) -> frozenset[str]`.
- Produces: `create_transaction_import(..., account: Account | None = None)` and persists `job.account_id`.
- Consumes: `list_workspace_accounts` and `get_workspace_account`.

- [ ] **Step 1: Write failing catalog and service tests**

```python
def test_transaction_categories_are_split_by_account_type() -> None:
    assert compatible_account_types("bank_transaction_statement") == frozenset(
        {"checking", "savings"}
    )
    assert compatible_account_types("credit_card_transaction_statement") == frozenset(
        {"credit_card"}
    )


def test_transaction_import_links_selected_account(session, workspace, tmp_path) -> None:
    account = create_account(
        session, workspace.id, AccountInput("Checking", "checking", "chase", "", False)
    )
    result = create_transaction_import(
        session,
        LocalUploadStore(tmp_path),
        extractor,
        workspace,
        "statement.csv",
        "text/csv",
        BytesIO(CSV),
        "retain",
        account=account,
    )
    assert result.job.account_id == account.id
```

Add route cases for missing account, foreign account, wrong account type, successful bank account,
successful credit-card account, and legacy alias without account.

- [ ] **Step 2: Run focused tests and verify expected failures**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\documents\test_catalog.py tests\documents\test_routes.py tests\test_import_service.py -q
```

Expected: split-category and `account_id` assertions fail.

- [ ] **Step 3: Implement server catalog and route validation**

Replace the visible combined category with the two split definitions. Add client catalog field
`compatible_account_types`. Keep a non-client alias lookup for `transaction_statement`.

Load the selected account only inside the workspace boundary, then validate:

```python
allowed = compatible_account_types(category.key)
if account is None:
    raise DocumentUploadValidationError("account_required", "Choose an account for this statement.")
if account.account_type not in allowed:
    raise DocumentUploadValidationError(
        "account_type_mismatch", "Choose an account that matches this statement type."
    )
```

- [ ] **Step 4: Persist the account on new and resumed imports**

Pass `account` through CSV and PDF creation. Include account id in duplicate-resume matching so the
same bytes attached to a different account do not silently resume another account's job. New jobs
set `account=account`.

- [ ] **Step 5: Add account selection to the queue**

Serialize workspace accounts into `client_config.accounts` as id, name, institution label, and type.
For transaction categories, render a required account select in the row, filter compatible options,
append `account_id` to `FormData`, and make readiness require a compatible selection. Non-transaction
rows do not send account id. Update the page copy to describe deterministic and optional AI
suggestions accurately.

- [ ] **Step 6: Run service, route, and browser tests and commit**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\documents tests\test_import_service.py -q
git add app/documents app/templates/documents/upload.html app/static/document-upload.js app/imports/service.py tests/documents tests/test_import_service.py
git commit -m "feat: link transaction uploads to accounts"
```

Expected: document and import tests pass.

### Task 3: Provider parser profiles and Chase automatic mapping

**Files:**
- Create: `app/imports/providers/__init__.py`
- Create: `app/imports/providers/types.py`
- Create: `app/imports/providers/chase.py`
- Create: `app/imports/providers/registry.py`
- Modify: `app/imports/service.py`
- Test: `tests/imports/providers/test_chase.py`
- Test: `tests/imports/providers/test_registry.py`
- Modify: `tests/test_import_service.py`
- Create: `tests/fixtures/statements/synthetic_chase_bank.csv`
- Create: `tests/fixtures/statements/synthetic_chase_credit_card.csv`

**Interfaces:**
- Produces: `ProviderProfile(key, institution_key, account_types, suffixes, required_headers, mapping)`.
- Produces: `resolve_provider_profile(institution_key, account_type, suffix, headers) -> ProviderResolution`.
- `ProviderResolution` contains `profile_key`, `mapping`, and `recognized`.

- [ ] **Step 1: Write failing Chase profile tests with synthetic headers**

```python
def test_chase_bank_profile_maps_official_export_headers() -> None:
    resolution = resolve_provider_profile(
        "chase",
        "checking",
        ".csv",
        ("Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"),
    )
    assert resolution.profile_key == "chase_bank_csv"
    assert resolution.mapping == ColumnMapping(
        "Posting Date", "Description", "single", "Amount", None, None, "mdy", "as_is"
    )


def test_unimplemented_institution_uses_generic_mapping() -> None:
    result = resolve_provider_profile("citi", "checking", ".csv", ("Date", "Memo", "Amount"))
    assert result.profile_key == "generic_csv"
    assert result.mapping is None
```

Add a Chase credit-card header/mapping test and a Chase-header-mismatch generic fallback test.

- [ ] **Step 2: Run provider tests and verify import errors**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\imports\providers -q
```

Expected: import/collection failure because provider modules do not exist.

- [ ] **Step 3: Implement immutable profile resolution**

Use exact header-set inclusion after trimming BOM/whitespace. Do not content-detect a different
institution. If the selected institution's tested profile does not recognize the headers, return
`generic_csv` with `recognized=False` and no mapping.

- [ ] **Step 4: Apply mapping when creating an account-linked CSV import**

Parse once after storage, resolve from the account, and create the job with `status="reviewing"`
and `column_mapping=resolution.mapping.to_json()` only when a predefined mapping exists. Generic
resolution retains `status="awaiting_mapping"`.

- [ ] **Step 5: Run provider and import tests and commit**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\imports\providers tests\test_import_service.py tests\test_csv_import_flow.py -q
git add app/imports/providers app/imports/service.py tests/imports/providers tests/test_import_service.py tests/fixtures/statements/synthetic_chase_bank.csv tests/fixtures/statements/synthetic_chase_credit_card.csv
git commit -m "feat: add Chase transaction parser profiles"
```

Expected: known Chase CSVs go directly to review; generic CSVs still go to mapping.

### Task 4: Chase sanitization and provider rules

**Files:**
- Create: `app/categorization/providers/__init__.py`
- Create: `app/categorization/providers/chase.py`
- Create: `app/categorization/sanitization.py`
- Modify: `app/categorization/types.py`
- Modify: `app/categorization/service.py`
- Test: `tests/categorization/test_sanitization.py`
- Test: `tests/categorization/providers/test_chase.py`
- Modify: `tests/categorization/test_service.py`

**Interfaces:**
- Produces: `sanitize_transaction_description(description: str) -> str` capped at 160 characters.
- Produces: `find_provider_rule(provider_key: str | None, description: str, amount_cents: int) -> ProviderMerchantRule | None`.
- Extends: `categorize_candidate(..., provider_key: str | None = None, ai_suggester: CategorizationSuggester | None = None)`.

- [ ] **Step 1: Write failing sanitizer and rule tests**

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BEST BUY AUTO PYMT 240812 123456789012345", "BEST BUY AUTO PYMT"),
        ("ZELLE PAYMENT TO JANE SAMPLE 123456789", "ZELLE PAYMENT TO <PAYEE>"),
        ("ZELLE PAYMENT FROM JOHN SAMPLE 987654321", "ZELLE PAYMENT FROM <PAYER>"),
    ],
)
def test_sanitizer_removes_identifying_suffixes(raw, expected) -> None:
    assert sanitize_transaction_description(raw) == expected


def test_best_buy_auto_payment_is_a_transfer() -> None:
    rule = find_provider_rule("chase_bank_csv", "BEST BUY AUTO PYMT 123456789", -2999)
    assert rule.category_name == "Transfers"
    assert rule.is_subscription is False
```

Add one case for every confirmed rule and negative cases proving Microsoft, Xoom, remote deposit,
and a partial substring do not match.

- [ ] **Step 2: Run tests and verify missing behavior fails**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization\test_sanitization.py tests\categorization\providers tests\categorization\test_service.py -q
```

Expected: missing modules/sources and provider precedence assertions fail.

- [ ] **Step 3: Implement conservative sanitizer and anchored rules**

Normalize NFKC/control characters/whitespace, replace Zelle party payloads, strip terminal reference
runs, and cap to 160 characters. Rules use `fullmatch`-style anchored expressions and emit only the
six confirmed decisions in the design.

- [ ] **Step 4: Insert provider precedence in categorization service**

Add `PROVIDER_RULE` and `AI_SUGGESTION` enum values. Resolve workspace rules first, then provider
rules, then built-ins. Convert category names through `_required_builtin_category` and preserve the
provider rule's normalized merchant and Subscription flag.

- [ ] **Step 5: Run categorization tests and commit**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization tests\transactions\test_categorization_service.py -q
git add app/categorization tests/categorization tests/transactions/test_categorization_service.py
git commit -m "feat: categorize confirmed Chase transaction patterns"
```

Expected: precedence and confirmed rule tests pass.

### Task 5: Privacy-bounded LangGraph/OpenAI classifier

**Files:**
- Create: `app/categorization/ai_types.py`
- Create: `app/categorization/ai_graph.py`
- Create: `app/categorization/openai_classifier.py`
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/categorization/test_ai_graph.py`
- Test: `tests/categorization/test_openai_classifier.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `CategorySuggestion(category_name: str, is_subscription: bool)`.
- Produces protocol: `DescriptionClassifier.classify(description, allowed_categories) -> ClassifierResult | None`.
- Produces: `build_categorization_graph(classifier) -> CompiledStateGraph`.
- Produces: `suggest_category(graph, description, allowed_categories) -> CategorySuggestion | None`.

- [ ] **Step 1: Add LangGraph and OpenAI dependencies**

```powershell
uv add "langgraph>=0.6,<2" "openai>=2,<3"
```

Expected: `pyproject.toml` and `uv.lock` contain bounded runtime dependencies.

- [ ] **Step 2: Write failing graph privacy and failure tests**

```python
def test_graph_sends_only_sanitized_description_and_allowlist() -> None:
    classifier = RecordingClassifier(CategorySuggestion("Transfers", False))
    graph = build_categorization_graph(classifier)
    result = suggest_category(
        graph,
        "ZELLE PAYMENT TO JANE SAMPLE 123456789",
        ("Housing", "Transfers", "Uncategorized"),
    )
    assert result == CategorySuggestion("Transfers", False)
    assert classifier.calls == [
        ("ZELLE PAYMENT TO <PAYEE>", ("Housing", "Transfers", "Uncategorized"))
    ]


@pytest.mark.parametrize("result", [None, ClassifierResult(None, False, True)])
def test_graph_abstention_returns_no_suggestion(result) -> None:
    assert (
        suggest_category(build_categorization_graph(FakeClassifier(result)), "UNKNOWN", CATEGORIES)
        is None
    )
```

Add cases for unknown category, empty sanitized text, exception, non-boolean schema rejection, and
160-character cap. Assert on the real graph output; fake only the external network boundary.

- [ ] **Step 3: Run graph tests and verify missing implementation fails**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization\test_ai_graph.py tests\categorization\test_openai_classifier.py tests\test_config.py -q
```

Expected: missing graph/config/adapter behavior fails.

- [ ] **Step 4: Implement graph and OpenAI adapter**

Build a `StateGraph` with sanitize, classify, and validate nodes. Catch classifier exceptions at the
graph boundary without logging descriptions. The OpenAI adapter calls `client.responses.parse` with
a strict Pydantic response model containing `category_name`, `is_subscription`, and `abstain`; its
system instruction treats the description as untrusted data and requests only allowlisted values.

- [ ] **Step 5: Add safe configuration and application injection**

```python
openai_api_key: str = ""
openai_categorization_model: str = "gpt-5.4-nano"
openai_categorization_enabled: bool = False
openai_categorization_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
```

Store either a compiled graph or `None` in `application.state.categorization_graph`. Instantiate the
OpenAI client only when enabled and a key is present. Document all variables in `.env.example`.

- [ ] **Step 6: Run AI/config tests and commit**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization\test_ai_graph.py tests\categorization\test_openai_classifier.py tests\test_config.py tests\test_app.py -q
git add app/categorization/ai_types.py app/categorization/ai_graph.py app/categorization/openai_classifier.py app/core/config.py app/main.py .env.example pyproject.toml uv.lock tests/categorization/test_ai_graph.py tests/categorization/test_openai_classifier.py tests/test_config.py tests/test_app.py
git commit -m "feat: add private LangGraph category suggestions"
```

Expected: tests use no API key/network and pass.

### Task 6: Integrate AI suggestions into review and verify the slice

**Files:**
- Modify: `app/categorization/service.py`
- Modify: `app/imports/service.py`
- Modify: `app/imports/routes.py`
- Modify: `app/templates/imports/review.html`
- Modify: `app/main.py`
- Modify: `README.md`
- Modify: `tests/categorization/test_import_integration.py`
- Modify: `tests/test_import_service.py`
- Modify: `tests/test_csv_import_flow.py`
- Modify: `tests/test_categorization_acceptance.py`

**Interfaces:**
- Consumes: `job.account.institution_key`, provider profile key, and optional app-state graph.
- Produces: provider/AI review sources, per-build sanitized-description memoization, and manual override persistence.

- [ ] **Step 1: Write failing import integration tests**

```python
def test_review_calls_ai_once_for_repeated_unresolved_description(...) -> None:
    graph = RecordingGraph(CategorySuggestion("Shopping", False))
    review = build_review(session, store, job, categorization_graph=graph)
    assert [row.categorization_source for row in review.rows] == [
        "ai_suggestion", "ai_suggestion"
    ]
    assert len(graph.calls) == 1

def test_provider_rule_never_calls_ai(...) -> None:
    review = build_review(session, store, chase_job, categorization_graph=FailIfCalledGraph())
    assert review.rows[0].categorization_source == "provider_rule"
```

Add tests for disabled AI, invalid direction discarded locally, AI source accepted on commit, manual
override stored as manual, and visible `AI suggestion`/`Provider rule` review badges.

- [ ] **Step 2: Run integration tests and verify missing integration fails**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization\test_import_integration.py tests\test_import_service.py tests\test_csv_import_flow.py tests\test_categorization_acceptance.py -q
```

Expected: new graph-call/source assertions fail.

- [ ] **Step 3: Integrate provider and AI context into `build_review`**

Determine profile key from the job account and source headers. Pass it to deterministic
categorization. For Uncategorized decisions, sanitize once, memoize `CategorySuggestion | None` by
sanitized value, validate the suggested category kind against local amount direction, and create an
`AI_SUGGESTION` decision. Do not include transaction fields in the graph invocation.

- [ ] **Step 4: Pass the graph from routes and render the source**

Read `request.app.state.categorization_graph` in review and commit rebuilds. Render human labels from
a server-defined mapping; do not display model names or raw output. Preserve signed review-token
semantics and ensure a changed field commits with source `manual`.

- [ ] **Step 5: Update operating documentation**

Document account-first upload, supported Chase CSV profiles, generic fallback, AI-off default,
environment variables, sent/not-sent data, and the fact that AI suggestions require review and do
not create merchant rules.

- [ ] **Step 6: Run focused, full, lint, and migration verification**

```powershell
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest tests\categorization tests\documents tests\accounts tests\imports tests\test_import_service.py tests\test_csv_import_flow.py tests\test_categorization_acceptance.py tests\test_provider_aware_migration.py -q
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m pytest -q
C:\repo\Where-Is-My-Money\.venv\Scripts\ruff.exe check .
C:\repo\Where-Is-My-Money\.venv\Scripts\python.exe -m alembic upgrade head
```

Expected: all tests pass, Ruff reports no errors, and Alembic reaches `0010`.

- [ ] **Step 7: Commit the integrated slice**

```powershell
git add app tests README.md docs/superpowers/specs/2026-08-13-provider-aware-transaction-categorization-design.md docs/superpowers/plans/2026-08-13-provider-aware-transaction-categorization.md
git commit -m "feat: integrate provider-aware transaction review"
```

Expected: the worktree is clean after the commit.
