# Workspace Rule System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a typed, efficient, explainable workspace rule system with lifecycle management, impact previews, safe historical application, audit records, and privacy-safe quality metrics across four stacked PRs.

**Architecture:** Keep provider, built-in, AI, and fallback precedence in `app/categorization`, but move workspace rule parsing, compilation, evaluation, management, previewing, and application into a focused `app/rules` package. Persist versioned condition trees as canonical JSON, compile them once per import, carry the winning rule ID through review and commit, and use bounded stale-safe confirmation for historical writes.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL-compatible schema, itsdangerous, Pytest, HTTPX, Playwright, Ruff.

## Global Constraints

- Work only in `C:\repo\Where-Is-My-Money\.worktrees\merchant-rules`; never edit, stage, commit, or switch the main checkout.
- Stack branches in order: `codex/merchant-rules-1-engine`, `codex/merchant-rules-2-management`, `codex/merchant-rules-3-history`, `codex/merchant-rules-4-insights`.
- Preserve precedence: manual transaction choice, ordered workspace rules, provider rules, built-in rules, optional AI suggestion, uncategorized.
- Manual categorizations must never be overwritten by historical rule application.
- Maximum condition depth is 4; maximum predicates per rule is 20; no regular expressions.
- Historical runs change at most 500 transactions and require explicit stale-safe confirmation.
- Never log or audit merchant descriptions, amounts, source filenames, file contents, tokens, or condition form payloads.
- Every production behavior begins with a focused failing test and follows red-green-refactor.
- Baseline is 906 passing tests, Ruff clean, and 247 formatted files.

## File Map

### PR 1 files

- `app/rules/types.py`: typed condition nodes, contexts, match explanations, compiled decisions.
- `app/rules/validation.py`: versioned JSON parsing, normalization, resource-independent validation, canonical serialization.
- `app/rules/evaluation.py`: pure predicate/tree evaluation and `CompiledWorkspaceRuleSet`.
- `app/rules/loader.py`: bounded SQLAlchemy loading of rules, categories, tags, and accounts.
- `migrations/versions/0013_workspace_rule_engine.py`: legacy conversion and transaction rule links.
- `app/db/models.py`: expanded `MerchantRule`, nullable transaction rule relationship.
- `app/categorization/types.py`: decision carries `merchant_rule_id` and explanation.
- `app/categorization/service.py`: delegates workspace matching to an optional compiled rule set.
- `app/imports/types.py`, `app/imports/review_tokens.py`, `app/imports/service.py`, `app/imports/routes.py`: rule ID propagation through review and commit.

### PR 2 files

- `app/rules/service.py`: CRUD, ordering, validation against workspace resources, optimistic concurrency, impact preview, simulator.
- `app/rules/presentation.py`: readable conditions/actions and metric-independent view models.
- `app/rules/routes.py`: authorized HTML routes and two-stage confirmation.
- `app/templates/rules/index.html`, `app/templates/rules/form.html`, `app/templates/rules/preview.html`, `app/templates/rules/delete.html`: management UI.
- `app/static/rules.js`: progressive condition-row enhancement only.
- `app/main.py`, `app/templates/base.html`, `app/static/styles.css`: router and navigation integration.

### PR 3 files

- `migrations/versions/0014_rule_application_runs.py`: redacted application audit.
- `app/db/models.py`: `RuleApplicationRun` relationships.
- `app/rules/application_tokens.py`: signed preview digest payload.
- `app/rules/service.py`: bounded history preview and atomic confirmed application.
- `app/rules/routes.py`: history preview and confirmation routes.
- `app/templates/rules/apply.html`, `app/templates/rules/apply_confirm.html`: selection and confirmation.

### PR 4 files

- `migrations/versions/0015_categorization_events.py`: privacy-safe correction events.
- `app/db/models.py`: `TransactionCategorizationEvent`.
- `app/rules/metrics.py`: bounded 90-day quality calculations.
- `app/rules/presentation.py`, `app/rules/routes.py`, rule and transaction templates: explanations and metrics.
- `app/transactions/service.py`: record manual correction events and clear rule attribution.
- `README.md`, `docs/architecture.md`: final behavior and operational documentation.

---

## PR 1 — Typed engine and batched evaluation

### Task 1: Migrate legacy rules and add attribution

**Files:**
- Create: `migrations/versions/0013_workspace_rule_engine.py`
- Modify: `app/db/models.py`
- Test: `tests/rules/test_migration.py`
- Test: `tests/rules/test_models.py`

**Interfaces:**
- Produces: expanded `MerchantRule` fields and `Transaction.merchant_rule_id` used by every later task.
- Migration converts each non-empty `merchant_pattern` into condition version 1 without changing its actions.

- [ ] **Step 1: Write the migration round-trip test**

```python
def test_workspace_rule_migration_converts_legacy_rule_and_round_trips(tmp_path: Path) -> None:
    engine = migration_engine(tmp_path)
    upgrade(engine, "0012_tax_refund_and_installment_tags")
    seed_legacy_rule(engine, workspace_id=1, merchant_pattern="NETFLIX COM")
    upgrade(engine, "0013_workspace_rule_engine")

    row = fetch_rule(engine)
    assert row["name"] == "NETFLIX COM"
    assert row["enabled"] == 1
    assert row["priority"] == 0
    assert json.loads(row["condition_json"]) == {
        "field": "merchant_key",
        "operator": "exact",
        "type": "predicate",
        "value": "NETFLIX COM",
        "version": 1,
    }

    downgrade(engine, "0012_tax_refund_and_installment_tags")
    assert fetch_legacy_pattern(engine) == "NETFLIX COM"
```

- [ ] **Step 2: Run the focused migration test and verify RED**

Run: `uv run pytest tests/rules/test_migration.py -q --basetemp tmp/pytest-pr1-migration`

Expected: FAIL because revision `0013_workspace_rule_engine` does not exist.

- [ ] **Step 3: Add the migration and ORM fields**

Implement revision `0013_workspace_rule_engine`, revising `0012_tax_refund_and_installment_tags`. Add rule columns with server defaults, backfill canonical JSON and per-workspace priority, make `merchant_pattern` nullable through `batch_alter_table`, add `transactions.merchant_rule_id` with `ON DELETE SET NULL`, and add indexes for `(workspace_id, enabled, priority)` and transaction rule ID. Add matching SQLAlchemy relationships and checks for non-negative priority, positive lock version, and condition version 1.

- [ ] **Step 4: Verify GREEN and metadata compatibility**

Run: `uv run pytest tests/rules/test_migration.py tests/rules/test_models.py tests/test_provider_aware_migration.py tests/test_tagging_migration.py -q --basetemp tmp/pytest-pr1-migration-green`

Expected: PASS.

- [ ] **Step 5: Commit the schema task**

```powershell
git add migrations/versions/0013_workspace_rule_engine.py app/db/models.py tests/rules/test_migration.py tests/rules/test_models.py
git commit -m "feat: migrate workspace rules to typed conditions"
```

### Task 2: Define and validate typed condition trees

**Files:**
- Create: `app/rules/__init__.py`
- Create: `app/rules/types.py`
- Create: `app/rules/validation.py`
- Create: `tests/rules/__init__.py`
- Create: `tests/rules/test_validation.py`

**Interfaces:**
- Produces: `RuleContext`, `PredicateCondition`, `AllCondition`, `AnyCondition`, `NotCondition`, `ConditionNode`, `parse_condition(payload)`, and `condition_to_json(node)`.

- [ ] **Step 1: Write validation tests for supported and rejected trees**

```python
def test_parse_condition_normalizes_a_typed_group() -> None:
    node = parse_condition(
        {
            "version": 1,
            "type": "all",
            "children": [
                {
                    "type": "predicate",
                    "field": "description",
                    "operator": "contains",
                    "value": "  Café  ",
                },
                {
                    "type": "not",
                    "child": {
                        "type": "predicate",
                        "field": "amount_cents",
                        "operator": "less_than",
                        "value": 0,
                    },
                },
            ],
        }
    )
    assert condition_to_payload(node)["children"][0]["value"] == "Café"


@pytest.mark.parametrize(
    "payload", [too_deep_tree(), too_many_predicates(), unknown_version(), empty_all_group()]
)
def test_parse_condition_rejects_unbounded_or_unknown_payloads(payload: object) -> None:
    with pytest.raises(RuleConditionValidationError):
        parse_condition(payload)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_validation.py -q --basetemp tmp/pytest-pr1-validation`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.rules'`.

- [ ] **Step 3: Implement immutable types and canonical validation**

Use frozen dataclasses and `Literal` aliases. Validate field/operator compatibility, positive account IDs, registered provider-key shape, ISO dates, integer cents, NFKC text, tree depth, and predicate count. Serialize with `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/rules/test_validation.py -q --basetemp tmp/pytest-pr1-validation-green`

Expected: PASS.

- [ ] **Step 5: Commit typed validation**

```powershell
git add app/rules tests/rules
git commit -m "feat: validate typed rule conditions"
```

### Task 3: Evaluate every typed predicate and composite

**Files:**
- Create: `app/rules/evaluation.py`
- Create: `tests/rules/test_evaluation.py`

**Interfaces:**
- Consumes: condition nodes and `RuleContext` from Task 2.
- Produces: `evaluate_condition(node, context) -> ConditionResult` with boolean match and child explanations.

- [ ] **Step 1: Write table-driven evaluator tests**

```python
@pytest.mark.parametrize(
    ("field", "operator", "value", "expected"),
    [
        ("description", "contains", "NETFLIX", True),
        ("merchant_key", "starts_with", "NETFLIX", True),
        ("amount_cents", "less_than", 0, True),
        ("transaction_date", "before", "2026-09-01", True),
        ("direction", "equal", "expense", True),
        ("account_id", "equal", 7, True),
        ("provider_key", "equal", "chase_bank_csv", True),
    ],
)
def test_typed_predicates(field: str, operator: str, value: object, expected: bool) -> None:
    result = evaluate_condition(predicate(field, operator, value), sample_context())
    assert result.matched is expected
    assert result.explanation
```

Add separate tests proving nested ALL/ANY/NOT behavior and fail-closed handling.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_evaluation.py -q --basetemp tmp/pytest-pr1-evaluation`

Expected: FAIL because `app.rules.evaluation` does not exist.

- [ ] **Step 3: Implement pure evaluation**

Implement case-insensitive normalized text comparisons, integer comparisons, `date.fromisoformat`, identity comparisons, recursive composite evaluation, and an immutable explanation tree. Do not query the database or mutate statistics.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/rules/test_evaluation.py tests/rules/test_validation.py -q --basetemp tmp/pytest-pr1-evaluation-green`

Expected: PASS.

- [ ] **Step 5: Commit evaluation**

```powershell
git add app/rules/evaluation.py tests/rules/test_evaluation.py
git commit -m "feat: evaluate typed workspace rule conditions"
```

### Task 4: Compile workspace rules with bounded queries

**Files:**
- Create: `app/rules/loader.py`
- Modify: `app/rules/evaluation.py`
- Modify: `app/categorization/types.py`
- Modify: `app/categorization/service.py`
- Test: `tests/rules/test_loader.py`
- Test: `tests/categorization/test_service.py`

**Interfaces:**
- Produces: `load_compiled_rule_set(session, workspace_id) -> CompiledWorkspaceRuleSet` and `CompiledWorkspaceRuleSet.match(context) -> WorkspaceRuleMatch | None`.
- Extends: `CategorizationDecision.merchant_rule_id: int | None` and `explanation: str | None`.

- [ ] **Step 1: Write ordering, authorization, and query-count tests**

```python
def test_compiled_rule_set_uses_priority_then_id_and_ignores_disabled(session, workspace) -> None:
    seed_overlapping_rules(session, workspace.id)
    compiled = load_compiled_rule_set(session, workspace.id)
    match = compiled.match(sample_context(description="NETFLIX COM"))
    assert match is not None
    assert match.rule.name == "Highest priority enabled"


def test_loading_rule_set_query_count_is_constant(session, workspace, query_counter) -> None:
    seed_rules(session, workspace.id, count=30)
    with query_counter(session.bind) as count:
        compiled = load_compiled_rule_set(session, workspace.id)
        for index in range(200):
            compiled.match(sample_context(description=f"MERCHANT {index}"))
    assert count.value <= 4
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_loader.py -q --basetemp tmp/pytest-pr1-loader`

Expected: FAIL because `load_compiled_rule_set` is missing.

- [ ] **Step 3: Implement the loader and categorization delegation**

Load workspace rules ordered by priority and ID with `selectinload(MerchantRule.tags)` and accessible categories/accounts in bulk. Parse once, retain invalid-rule diagnostics, and update `categorize_candidate(..., workspace_rules: CompiledWorkspaceRuleSet | None = None)` to use the compiled set before provider rules. Preserve the current one-off query path temporarily by lazily loading when callers omit the set.

- [ ] **Step 4: Verify GREEN and existing precedence**

Run: `uv run pytest tests/rules/test_loader.py tests/categorization/test_service.py tests/categorization/test_import_integration.py -q --basetemp tmp/pytest-pr1-loader-green`

Expected: PASS.

- [ ] **Step 5: Commit compilation**

```powershell
git add app/rules app/categorization tests/rules tests/categorization
git commit -m "feat: compile workspace rules for deterministic matching"
```

### Task 5: Carry rule attribution through import review and commit

**Files:**
- Modify: `app/imports/types.py`
- Modify: `app/imports/review_tokens.py`
- Modify: `app/imports/service.py`
- Modify: `app/imports/routes.py`
- Test: `tests/test_import_service.py`
- Test: `tests/test_import_routes.py`
- Test: `tests/rules/test_import_performance.py`

**Interfaces:**
- Extends: `ReviewRow`, `RowEdit`, and `ReviewBaseline` with `merchant_rule_id: int | None`.
- Import commit persists `Transaction.merchant_rule_id` only for unchanged workspace-rule decisions.

- [ ] **Step 1: Write attribution and performance tests**

```python
def test_workspace_rule_id_survives_review_token_and_commit(session, workspace, store) -> None:
    rule = seed_workspace_rule(session, workspace.id, "NETFLIX")
    job = mapped_job_with_row(session, workspace, store, "Netflix.com")
    review = build_review(session, store, job)
    assert review.rows[0].merchant_rule_id == rule.id
    commit_import(session, store, job, unchanged_edits(review))
    transaction = session.scalar(select(Transaction))
    assert transaction.merchant_rule_id == rule.id


def test_import_rule_queries_do_not_scale_with_rows(
    session, workspace, store, query_counter
) -> None:
    job = mapped_job_with_many_rows(session, workspace, store, count=250)
    with query_counter(session.bind, table="merchant_rules") as count:
        build_review(session, store, job)
    assert count.value == 1
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_import_performance.py tests/test_import_service.py -q --basetemp tmp/pytest-pr1-import`

Expected: FAIL because review rows and transactions do not carry rule IDs and imports reload rules per row.

- [ ] **Step 3: Implement propagation and one-load-per-review**

Bump signed review token version to 3, validate nullable positive rule IDs, preserve the ID only when the submitted action equals the signed baseline, clear it for manual edits, and instantiate one compiled set before the review row loop. Persist the ID with each committed transaction.

- [ ] **Step 4: Verify PR 1**

Run: `uv run pytest tests/rules tests/categorization tests/test_import_service.py tests/test_import_routes.py tests/test_csv_import_flow.py -q --basetemp tmp/pytest-pr1-complete`

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Expected: all commands pass.

- [ ] **Step 5: Commit and mark PR 1 boundary**

```powershell
git add app/imports app/db/models.py tests
git commit -m "feat: preserve workspace rule attribution through imports"
git tag merchant-rules-pr1-ready
```

---

## PR 2 — Rule management and impact preview

### Task 6: Create the stacked PR 2 branch and CRUD service

**Files:**
- Create branch: `codex/merchant-rules-2-management`
- Create: `app/rules/service.py`
- Create: `tests/rules/test_service.py`

**Interfaces:**
- Produces: `RuleDraft`, `create_rule`, `update_rule`, `set_rule_enabled`, `move_rule`, `duplicate_rule`, `delete_rule`, and workspace-scoped lookup errors.

- [ ] **Step 1: Create the stacked branch**

Run: `git switch -c codex/merchant-rules-2-management`

Expected: branch starts at the verified PR 1 boundary.

- [ ] **Step 2: Write failing CRUD and concurrency tests**

```python
def test_update_rule_rejects_stale_lock_version(session, workspace) -> None:
    rule = seed_workspace_rule(session, workspace.id, "NETFLIX")
    with pytest.raises(RuleConflictError):
        update_rule(session, workspace.id, rule.id, draft_for(rule), expected_lock_version=0)
    session.refresh(rule)
    assert rule.lock_version == 1


def test_move_rule_compacts_workspace_priorities_atomically(session, workspace) -> None:
    rules = seed_rules(session, workspace.id, count=3)
    move_rule(session, workspace.id, rules[2].id, new_index=0)
    assert [(rule.id, rule.priority) for rule in list_rules(session, workspace.id)] == [
        (rules[2].id, 0),
        (rules[0].id, 1),
        (rules[1].id, 2),
    ]
```

- [ ] **Step 3: Verify RED**

Run: `uv run pytest tests/rules/test_service.py -q --basetemp tmp/pytest-pr2-service`

Expected: FAIL because the service functions are missing.

- [ ] **Step 4: Implement workspace-scoped lifecycle operations**

Validate names, actions, condition resources, accessible categories/tags/accounts, provider keys, lock versions, and ordering. Every mutation flushes inside the caller's transaction; routes own commit/rollback. Delete compacts priorities and relies on `ON DELETE SET NULL`.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/rules/test_service.py tests/categorization/test_service.py -q --basetemp tmp/pytest-pr2-service-green`

```powershell
git add app/rules/service.py tests/rules/test_service.py
git commit -m "feat: manage workspace rule lifecycle"
```

### Task 7: Build readable summaries, simulator, and impact preview

**Files:**
- Create: `app/rules/presentation.py`
- Modify: `app/rules/service.py`
- Create: `tests/rules/test_presentation.py`
- Create: `tests/rules/test_preview.py`

**Interfaces:**
- Produces: `describe_condition`, `describe_actions`, `simulate_rules`, and `preview_rule_impact` returning bounded counts, conflicts, and at most 20 examples.

- [ ] **Step 1: Write failing summary and preview tests**

```python
def test_preview_reports_changes_manual_protection_and_shadowing(session, workspace) -> None:
    higher, draft = seed_preview_scenario(session, workspace.id)
    preview = preview_rule_impact(session, workspace.id, draft, exclude_rule_id=None)
    assert preview.would_change_count == 2
    assert preview.manual_skip_count == 1
    assert preview.conflict_skip_count == 1
    assert preview.conflicts[0].winning_rule_id == higher.id
    assert len(preview.examples) <= 20
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_presentation.py tests/rules/test_preview.py -q --basetemp tmp/pytest-pr2-preview`

Expected: FAIL because presentation and preview APIs do not exist.

- [ ] **Step 3: Implement bounded read-only analysis**

Build readable recursive summaries, simulate without writes, project only required transaction columns, exclude manual transactions from change eligibility, evaluate all matching workspace rules to detect shadows, group counts by category/account, and sanitize examples with `sanitize_transaction_description`.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/rules/test_presentation.py tests/rules/test_preview.py -q --basetemp tmp/pytest-pr2-preview-green`

```powershell
git add app/rules/presentation.py app/rules/service.py tests/rules
git commit -m "feat: preview and explain workspace rule impact"
```

### Task 8: Add authorized rule-management routes and templates

**Files:**
- Create: `app/rules/routes.py`
- Create: `app/templates/rules/index.html`
- Create: `app/templates/rules/form.html`
- Create: `app/templates/rules/preview.html`
- Create: `app/templates/rules/delete.html`
- Create: `app/static/rules.js`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/static/styles.css`
- Create: `tests/rules/test_routes.py`
- Create: `tests/rules/test_browser.py`

**Interfaces:**
- Produces authorized GET/POST routes beneath `/workspaces/{workspace_id}/rules` for list, preview, confirm save, edit, duplicate, move, enable, simulator, and delete.

- [ ] **Step 1: Write failing authorization and two-stage-save tests**

```python
async def test_rule_create_requires_preview_then_confirmation(tmp_path: Path) -> None:
    app, factory, engine = build_route_test_app(tmp_path)
    async with signed_in_client(app) as client:
        workspace_id = workspace_id_for(factory)
        preview = await client.post(
            f"/workspaces/{workspace_id}/rules/preview",
            data=valid_rule_form(csrf=await csrf_token(client)),
        )
        assert preview.status_code == 200
        assert "2 transactions would change" in preview.text
        saved = await client.post(
            f"/workspaces/{workspace_id}/rules",
            data=confirmation_form(preview.text, csrf=await csrf_token(client)),
            follow_redirects=False,
        )
        assert saved.status_code == 303
```

Add CSRF, foreign workspace, stale lock, reorder, disabled, delete, no-JavaScript, keyboard, and mobile viewport tests.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_routes.py -q --basetemp tmp/pytest-pr2-routes`

Expected: FAIL with 404 because the rules router is not registered.

- [ ] **Step 3: Implement routes and accessible server-rendered UI**

Follow existing route context and workspace dependencies. Parse indexed condition rows server-side, render field-specific controls, include a signed normalized draft in confirmation, return 409 for stale edits, and keep every core operation available without JavaScript. JavaScript may add/remove rows and refresh visible inputs only.

- [ ] **Step 4: Verify PR 2**

Run: `uv run pytest tests/rules tests/test_shared_navigation.py -q --basetemp tmp/pytest-pr2-complete`

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Expected: all commands pass.

- [ ] **Step 5: Commit and mark PR 2 boundary**

```powershell
git add app tests/rules tests/test_shared_navigation.py
git commit -m "feat: add workspace rule management"
git tag merchant-rules-pr2-ready
```

---

## PR 3 — Safe historical application

### Task 9: Add application audit persistence and signed previews

**Files:**
- Create branch: `codex/merchant-rules-3-history`
- Create: `migrations/versions/0014_rule_application_runs.py`
- Modify: `app/db/models.py`
- Create: `app/rules/application_tokens.py`
- Create: `tests/rules/test_application_migration.py`
- Create: `tests/rules/test_application_tokens.py`

**Interfaces:**
- Produces: `RuleApplicationRun`, `create_application_token`, and `load_application_token` with workspace, rule version, selected IDs, state digest, and normalized filters.

- [ ] **Step 1: Create the stacked branch**

Run: `git switch -c codex/merchant-rules-3-history`

- [ ] **Step 2: Write failing migration and tamper tests**

```python
def test_application_token_rejects_changed_transaction_state() -> None:
    token = create_application_token(SECRET, preview_payload(state_digest="abc"))
    payload = load_application_token(SECRET, token)
    assert payload.state_digest == "abc"
    with pytest.raises(RuleApplicationTokenError):
        load_application_token(SECRET, token + "tampered")
```

- [ ] **Step 3: Verify RED**

Run: `uv run pytest tests/rules/test_application_migration.py tests/rules/test_application_tokens.py -q --basetemp tmp/pytest-pr3-audit`

Expected: FAIL because revision 0014, model, and token module are absent.

- [ ] **Step 4: Implement redacted audit and signed versioned token**

Create the audit table, relationships, state/count checks, and a timed itsdangerous serializer with a dedicated salt and one-hour age. Validate no more than 500 distinct positive transaction IDs and exact payload types.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/rules/test_application_migration.py tests/rules/test_application_tokens.py -q --basetemp tmp/pytest-pr3-audit-green`

```powershell
git add migrations/versions/0014_rule_application_runs.py app/db/models.py app/rules/application_tokens.py tests/rules
git commit -m "feat: audit historical rule applications"
```

### Task 10: Preview and atomically apply bounded history changes

**Files:**
- Modify: `app/rules/service.py`
- Create: `tests/rules/test_history_service.py`

**Interfaces:**
- Produces: `HistoryFilters`, `preview_historical_application`, and `confirm_historical_application`.

- [ ] **Step 1: Write failing safety and atomicity tests**

```python
def test_confirm_history_protects_manual_and_rejects_stale_digest(session, workspace) -> None:
    rule, automatic, manual = seed_history_scenario(session, workspace.id)
    preview = preview_historical_application(session, workspace.id, rule.id, HistoryFilters())
    automatic.category_id = another_category(session).id
    session.flush()
    with pytest.raises(StaleRuleApplicationError):
        confirm_historical_application(
            session, workspace.id, preview.token, user_id=workspace.owner_id
        )
    session.refresh(manual)
    assert manual.categorization_source == "manual"


def test_confirm_history_is_idempotent_and_all_or_nothing(session, workspace) -> None:
    preview = seeded_history_preview(session, workspace.id, eligible_count=3)
    first = confirm_historical_application(session, workspace.id, preview.token, workspace.owner_id)
    second = confirm_historical_application(
        session, workspace.id, preview.token, workspace.owner_id
    )
    assert second.run_id == first.run_id
    assert second.changed_count == 3
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_history_service.py -q --basetemp tmp/pytest-pr3-history`

Expected: FAIL because history service APIs are missing.

- [ ] **Step 3: Implement preview, digest, and application**

Normalize filters, query authorized candidates in bounded pages, evaluate the full workspace set to identify the winner, exclude manual and shadowed rows, cap selected changes at 500, digest current and resulting categorization fields plus sorted tag IDs, create a preview audit row, and on confirmation recompute before applying all action fields in one transaction. A confirmed run returns its stored result on retry.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/rules/test_history_service.py tests/rules/test_preview.py -q --basetemp tmp/pytest-pr3-history-green`

```powershell
git add app/rules/service.py tests/rules/test_history_service.py
git commit -m "feat: safely apply rules to transaction history"
```

### Task 11: Add historical preview and confirmation UI

**Files:**
- Modify: `app/rules/routes.py`
- Create: `app/templates/rules/apply.html`
- Create: `app/templates/rules/apply_confirm.html`
- Create: `tests/rules/test_history_routes.py`
- Modify: `tests/rules/test_browser.py`

**Interfaces:**
- Adds GET/POST routes for history filters, row selection, confirmation, and idempotent result display.

- [ ] **Step 1: Write failing route and browser tests**

```python
async def test_history_confirmation_rejects_foreign_selection_and_preserves_rows(
    tmp_path: Path,
) -> None:
    app, factory, engine = build_route_test_app(tmp_path)
    local, foreign = seed_cross_workspace_transactions(factory)
    async with signed_in_client(app) as client:
        response = await client.post(
            history_confirm_url(local.workspace_id),
            data={
                "transaction_ids": [local.id, foreign.id],
                "csrf_token": await csrf_token(client),
            },
        )
    assert response.status_code == 404
    assert unchanged(factory, local.id, foreign.id)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_history_routes.py -q --basetemp tmp/pytest-pr3-routes`

Expected: FAIL because the history routes do not exist.

- [ ] **Step 3: Implement bounded filters, selection, and confirmation pages**

Render change/unchanged/manual/conflict counts, selected eligible rows, the 500-row limit, and explicit action effects. Reuse signed preview tokens, generic 404 boundaries, CSRF, 409 stale responses, and truthful idempotent results.

- [ ] **Step 4: Verify PR 3 and mark boundary**

Run: `uv run pytest tests/rules -q --basetemp tmp/pytest-pr3-complete`

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

```powershell
git add app/rules app/templates/rules tests/rules
git commit -m "feat: confirm historical rule application"
git tag merchant-rules-pr3-ready
```

---

## PR 4 — Explainability, metrics, and hardening

### Task 12: Record redacted correction events

**Files:**
- Create branch: `codex/merchant-rules-4-insights`
- Create: `migrations/versions/0015_categorization_events.py`
- Modify: `app/db/models.py`
- Modify: `app/transactions/service.py`
- Modify: `app/imports/service.py`
- Modify: `app/rules/service.py`
- Create: `tests/rules/test_events.py`

**Interfaces:**
- Produces `TransactionCategorizationEvent` and `record_categorization_event` for manual, import, and historical sources without financial text/value fields.

- [ ] **Step 1: Create branch and write failing privacy tests**

Run: `git switch -c codex/merchant-rules-4-insights`

```python
def test_manual_correction_event_contains_sources_and_ids_only(session, workspace) -> None:
    transaction = seeded_rule_transaction(session, workspace.id)
    manually_categorize_transaction(session, workspace.id, transaction.id, manual_input())
    event = session.scalar(select(TransactionCategorizationEvent))
    assert event.previous_source == "workspace_rule"
    assert event.new_source == "manual"
    assert event.previous_rule_id is not None
    assert not hasattr(event, "description")
    assert not hasattr(event, "amount_cents")
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_events.py -q --basetemp tmp/pytest-pr4-events`

Expected: FAIL because revision 0015 and the event model are absent.

- [ ] **Step 3: Implement event persistence at successful mutation boundaries**

Add the redacted table and record events only when persisted categorization source or rule attribution changes. Manual edits clear `merchant_rule_id`; historical applications and import commits attach the winning rule when available.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/rules/test_events.py tests/transactions/test_categorization_routes.py tests/test_import_service.py -q --basetemp tmp/pytest-pr4-events-green`

```powershell
git add migrations/versions/0015_categorization_events.py app tests/rules/test_events.py
git commit -m "feat: record redacted categorization events"
```

### Task 13: Add explanations and bounded quality metrics

**Files:**
- Create: `app/rules/metrics.py`
- Modify: `app/rules/presentation.py`
- Modify: `app/rules/routes.py`
- Modify: `app/transactions/queries.py`
- Modify: `app/templates/rules/index.html`
- Modify: `app/templates/transactions/list.html`
- Modify: `app/templates/transactions/edit.html`
- Create: `tests/rules/test_metrics.py`
- Create: `tests/rules/test_explanations.py`
- Modify: `tests/rules/test_routes.py`

**Interfaces:**
- Produces: `build_rule_metrics(session, workspace_id, as_of_date) -> RuleMetricsReport` and truthful transaction source presentation.

- [ ] **Step 1: Write failing bounded metric and deleted-rule tests**

```python
def test_rule_metrics_use_90_day_window_and_real_correction_events(session, workspace) -> None:
    seed_metric_history(session, workspace.id)
    report = build_rule_metrics(session, workspace.id, date(2026, 8, 15))
    assert report.window_start == date(2026, 5, 18)
    assert report.uncategorized_rate_basis_points == 1250
    assert report.manual_correction_rate_basis_points == 500


def test_deleted_rule_transaction_is_presented_truthfully(session, workspace) -> None:
    transaction = seeded_deleted_rule_transaction(session, workspace.id)
    explanation = transaction_explanation(transaction)
    assert explanation.source_label == "Deleted workspace rule"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/rules/test_metrics.py tests/rules/test_explanations.py -q --basetemp tmp/pytest-pr4-metrics`

Expected: FAIL because metrics and explanation APIs are absent.

- [ ] **Step 3: Implement bounded metrics and source presentation**

Calculate linked count, last committed use, 90-day matches, conflicts, protected manual matches, coverage by source, uncategorized rate, and correction rate from projected columns and redacted events. Render rule links when present and the deleted-rule label when the source is workspace rule but the FK is null. Metrics errors return `None` and do not block the rule page.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/rules/test_metrics.py tests/rules/test_explanations.py tests/rules/test_routes.py tests/test_transaction_routes.py -q --basetemp tmp/pytest-pr4-metrics-green`

```powershell
git add app/rules app/transactions app/templates tests/rules tests/test_transaction_routes.py
git commit -m "feat: explain and measure workspace rules"
```

### Task 14: Document, harden, and verify the complete stack

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `tests/rules/test_browser.py`
- Modify: `tests/test_production_readiness.py`
- Modify: `.github/workflows/ci.yml` only if the existing commands do not already cover the new tests.

**Interfaces:**
- Produces the final documented and fully verified branch `codex/merchant-rules-4-insights`.

- [ ] **Step 1: Add final browser and operational assertions before documentation**

```python
def test_rules_pages_have_no_horizontal_overflow(page, live_server, signed_in_workspace) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server}/workspaces/{signed_in_workspace.id}/rules")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert overflow is False
```

Add browser assertions for keyboard create/edit, disabled rules, preview confirmation, stale history, metric labels, and deleted attribution.

- [ ] **Step 2: Run the new complete rule suite**

Run: `uv run pytest tests/rules -q --basetemp tmp/pytest-pr4-rules`

Expected: PASS.

- [ ] **Step 3: Update README and architecture with exact behavior**

Document condition fields/operators, precedence, rule lifecycle, import compilation, impact preview, 500-change history boundary, manual protection, audit privacy, metrics window, and all four branch boundaries. Keep setup commands unchanged unless verification proves a required change.

- [ ] **Step 4: Run fresh migration verification**

Run: `uv run alembic upgrade head`

Run: `uv run pytest tests/rules/test_migration.py tests/rules/test_application_migration.py tests/rules/test_events.py -q --basetemp tmp/pytest-pr4-migrations`

Expected: migration reaches `0015_categorization_events` and round-trip tests pass.

- [ ] **Step 5: Run final full verification**

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

Run: `uv run pytest -q --basetemp tmp/pytest-final`

Expected: all checks pass with more than the 906-test baseline and no warnings attributable to the implementation.

- [ ] **Step 6: Commit final documentation and verification boundary**

```powershell
git add README.md docs/architecture.md tests/rules tests/test_production_readiness.py .github/workflows/ci.yml
git commit -m "docs: complete workspace rule system"
git tag merchant-rules-pr4-ready
```

## Final Review Checklist

- PR 1 owns schema, typed evaluation, batching, precedence, and import attribution.
- PR 2 owns lifecycle management, readable builder, previews, simulator, and browser-accessible UI.
- PR 3 owns bounded historical selection, stale-safe confirmation, atomic changes, idempotency, and redacted audit.
- PR 4 owns correction events, explanations, metrics, documentation, and full-stack verification.
- No branch changes main; every branch is stacked from its predecessor.
- No approved requirement is deferred beyond PR 4.
