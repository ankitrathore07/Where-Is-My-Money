# Workspace Rule System Design

**Date:** 2026-08-15  
**Status:** Approved for implementation  
**Delivery:** Four stacked pull-request branches, beginning with `codex/merchant-rules-1-engine`

## Purpose

Upgrade exact-key merchant rules into a safe, explainable workspace rule system without weakening the current privacy, authorization, review, or categorization boundaries. The complete system must support typed composite conditions, efficient import-time evaluation, rule lifecycle management, impact previews, confirmed historical application, auditability, and rule-quality measurements.

The implementation borrows useful interaction ideas from a separately reviewed desktop finance application, but does not copy its code or its unsafe techniques. In particular, this design forbids reflection-based serialization, display-text parsing, fake amount/date predicates evaluated against description strings, unconfirmed automatic rules, and database rule reloads for every transaction.

## Product Principles

1. Manual categorization remains the highest authority.
2. Every write remains workspace-scoped and human-confirmed.
3. Rules explain their inputs, match result, winning priority, and actions.
4. Imports load and compile rules once, then evaluate rows in memory.
5. Existing exact merchant rules retain their behavior after migration.
6. Historical changes are bounded, previewed, stale-safe, atomic, and audited.
7. Invalid or unsupported rules fail closed.
8. Financial values, descriptions, and source-file contents never enter operational logs or audit metadata.

## Categorization Precedence

The top-level precedence remains:

1. A transaction's explicit manual categorization
2. Enabled workspace rules, ordered by ascending priority and then ID
3. Confirmed provider-specific rules
4. Built-in exact merchant rules
5. Optional AI suggestion when the deterministic result is uncategorized
6. Uncategorized fallback

Priority changes only ordering within workspace rules. A workspace rule can never be reordered below provider or built-in behavior.

## Data Model

### MerchantRule

Extend `merchant_rules` with:

- `name`: required display name, maximum 120 characters
- `enabled`: boolean, default `true`
- `priority`: non-negative integer
- `condition_version`: integer, initially `1`
- `condition_json`: required canonical JSON condition tree
- `lock_version`: positive integer used for optimistic concurrency

Keep the existing action fields:

- `normalized_merchant`
- `category_id`
- rule tags
- `is_subscription`
- `billing_period_months`

Make `merchant_pattern` nullable and retain it only as a compatibility/indexing aid for legacy exact merchant rules. New evaluation uses `condition_json`.

Existing rules migrate to enabled, ordered rules with generated names and this exact condition:

```json
{
  "version": 1,
  "type": "predicate",
  "field": "merchant_key",
  "operator": "exact",
  "value": "THE EXISTING MERCHANT PATTERN"
}
```

Migration ordering is deterministic by `workspace_id`, `created_at`, and `id`.

### Transaction

Add nullable `merchant_rule_id` referencing `merchant_rules.id` with `ON DELETE SET NULL` and an index. A deleted rule never changes the transaction's current category or other action fields. Its explanation becomes “Deleted workspace rule.”

### RuleApplicationRun

PR 3 adds `rule_application_runs` with:

- workspace, nullable rule, and initiating user IDs
- rule name snapshot and rule version
- status: `previewed`, `confirmed`, `stale`, or `failed`
- normalized selection JSON and signed preview digest
- matched, changed, unchanged, manual-skip, and conflict-skip counts
- created and confirmed timestamps

The audit row stores identifiers, rule metadata, filters, and counts. It must not store descriptions, merchants, amounts, source filenames, or file contents.

### TransactionCategorizationEvent

PR 4 adds a redacted categorization event containing:

- workspace and transaction IDs
- previous and new categorization sources
- previous and new nullable rule IDs
- reason enum
- timestamp

It contains no merchant text or financial values and enables an honest manual-correction rate.

## Typed Conditions

### RuleContext

Rules evaluate a typed immutable context:

```python
@dataclass(frozen=True)
class RuleContext:
    description: str
    merchant_key: str
    amount_cents: int
    transaction_date: date
    direction: Literal["income", "expense", "zero"]
    account_id: int | None
    provider_key: str | None
```

### Condition nodes

Version 1 supports:

- `predicate`: one field/operator/value comparison
- `all`: every child must match
- `any`: at least one child must match
- `not`: negates exactly one child

Supported fields and operators:

| Field | Operators | Stored value |
| --- | --- | --- |
| `description` | `exact`, `contains`, `starts_with`, `ends_with` | NFKC-normalized text |
| `merchant_key` | `exact`, `contains`, `starts_with`, `ends_with` | canonical merchant key |
| `amount_cents` | `equal`, `greater_than`, `greater_or_equal`, `less_than`, `less_or_equal` | integer cents |
| `transaction_date` | `on`, `before`, `after` | ISO date |
| `direction` | `equal` | `income`, `expense`, or `zero` |
| `account_id` | `equal` | positive integer |
| `provider_key` | `equal` | registered provider key |

Category is intentionally excluded as an input predicate because categorization rules execute before the category action is chosen. Category remains an action and a preview filter.

### Complexity and validation limits

- Maximum tree depth: 4
- Maximum predicate count: 20
- Maximum normalized text value: 255 characters
- No regular expressions
- Account IDs must belong to the active workspace
- Provider keys must exist in the provider registry
- Unknown node types, fields, operators, or condition versions fail closed
- Empty groups and malformed NOT nodes are invalid
- Canonical JSON uses stable key ordering and compact separators

## Architecture

Create a focused `app/rules/` feature package:

```text
app/rules/
├── __init__.py
├── types.py          # Condition nodes, RuleContext, preview/result types
├── validation.py     # Parse, normalize, validate, and serialize conditions
├── evaluation.py     # Pure evaluation and CompiledWorkspaceRuleSet
├── service.py        # CRUD, ordering, previews, historical application
├── routes.py         # Authorized HTML form and confirmation routes
└── presentation.py   # IF/THEN summaries, explanations, and metrics
```

`app/categorization/service.py` keeps provider, built-in, AI, and fallback precedence. It delegates workspace matching to a compiled rule set.

`CompiledWorkspaceRuleSet` bulk-loads enabled rules, accessible categories, tags, and referenced accounts. It validates and parses each rule once, orders rules by priority and ID, evaluates rows in memory, and returns the full categorization decision plus the winning rule ID and explanation.

Import review tokens carry the winning rule ID. Commit validates that the rule is still accessible and stores `merchant_rule_id` with `categorization_source = workspace_rule`. Preview decisions remain stable even if a rule changes after preview, matching the existing review contract; a stale or deleted rule link becomes null rather than silently reevaluating the committed row.

## Management User Experience

Add `Rules` to authenticated workspace navigation and expose `/workspaces/{workspace_id}/rules`.

The list displays:

- name and enabled state
- human-readable IF/THEN summary
- execution priority
- linked transaction count and last committed use
- conflict warning
- edit, duplicate, move, enable/disable, test, and delete controls

The builder supports one top-level ALL or ANY group with up to 20 rows and a per-row NOT control. The persisted engine supports arbitrary valid nesting so future or imported rule trees remain compatible. The builder uses field-specific controls and works as a normal server form without JavaScript; small local JavaScript may progressively enhance row addition and summaries.

### Two-stage save

1. Validate condition and action inputs.
2. Calculate an authorized impact preview.
3. Show match, change, unchanged, manual-protection, and conflict counts.
4. Show counts by current category and account plus at most 20 authorized sanitized examples.
5. Require explicit save confirmation.
6. Recheck `lock_version` and persist atomically.

Every edit submits the current `lock_version`. A mismatch returns `409 Conflict` without mutation. Successful edits increment the version.

Priorities compact after create, move, or delete. Ties are resolved by ID. Reordering occurs in one transaction.

Deleting a rule requires confirmation, preserves historical action values, and nulls transaction links. The confirmation reports linked transaction and fallback-match counts.

## Simulator

The Rules page accepts synthetic or manually entered description, amount, date, account, and provider values. It reports:

- every matching workspace rule in order
- the winning workspace rule
- provider or built-in fallback when no workspace rule wins
- resulting actions
- a predicate-by-predicate explanation

Simulation is read-only and never changes hit statistics.

## Historical Application

Historical application operates on one saved rule at a time. Preview divides authorized transactions into:

- eligible and would change
- eligible but already identical
- matched but shadowed by a higher-priority workspace rule
- manually categorized and protected
- invalid because a referenced action resource no longer exists
- not matched

Manual categorizations are never overwritten.

Users can filter by date range, account, direction, and current category, then select individual eligible rows. A confirmed run changes at most 500 transactions; larger changes require narrower filters or multiple runs.

The signed preview digest covers workspace, rule ID and lock version, selected transaction IDs, existing categorization state, resulting action state, and normalized filters. Confirmation reloads the rule and transactions, recomputes the digest, and returns `409 Conflict` on any mismatch.

Confirmed application atomically updates merchant, category, tags, subscription, cadence, source, and rule link. It preserves date, description, amount, import job, and duplicate fingerprint. Retrying a confirmed run returns the original outcome.

## Explainability and Metrics

Transaction views show categorization source, linked rule name, a human-readable match explanation, and fallback source. Deleted rule links render honestly.

The Rules page shows:

- linked transaction count
- last committed use
- 90-day match count
- higher-priority conflict count
- protected manual match count
- later manual-correction percentage

Workspace quality metrics show workspace-rule, provider/built-in, and AI coverage; uncategorized rate; manual correction rate; and conflicting-rule rate. Metrics use bounded 90-day windows and projected columns. Preview and simulation never increment production statistics.

## Error Handling

- Invalid or unsupported condition: exclude from evaluation and surface repair state
- Missing action resource: reject save or application
- Foreign resource or transaction: generic `404`
- Stale edit or preview: `409 Conflict`, no mutation
- More than 500 selected changes: `422 Unprocessable Entity`
- Concurrent reorder or delete: roll back and reload current state
- Audit failure: roll back historical application
- Metrics failure: omit metrics without blocking core management
- Invalid rule encountered during import: fail closed to the next precedence layer and emit only safe operational metadata

## Delivery Sequence

### PR 1 — `codex/merchant-rules-1-engine`

- Commit this design and the full implementation plan
- Add typed conditions, validation, migration, compiled evaluation, and rule links
- Preserve legacy behavior and precedence
- Eliminate per-row rule queries
- Add migration, evaluator, precedence, compatibility, and query-count tests

### PR 2 — `codex/merchant-rules-2-management`

- Add CRUD, enable/disable, ordering, duplication, and deletion
- Add the rule builder and IF/THEN presentation
- Add two-stage impact/conflict preview and simulator
- Add authorization, CSRF, concurrency, accessibility, and browser tests

### PR 3 — `codex/merchant-rules-3-history`

- Add bounded historical preview and selection
- Add signed stale-safe confirmation and atomic application
- Protect manual categorizations
- Add redacted application audit and idempotency
- Add rollback, stale, concurrency, authorization, and browser tests

### PR 4 — `codex/merchant-rules-4-insights`

- Add transaction explanations and deleted-rule states
- Add correction events, per-rule metrics, and workspace quality metrics
- Add bounded metrics and performance regression tests
- Update README and architecture documentation
- Run the complete verification suite

Branches are stacked in that order. The main checkout is never modified or switched.

## Testing Contract

Implementation follows test-driven development. Every new behavior begins with a focused failing test that fails for the missing behavior, followed by the minimum implementation and a green targeted suite.

Required coverage includes:

- migration upgrade, downgrade, and deterministic legacy conversion
- every field/operator pair and nested ALL/ANY/NOT behavior
- complexity limits, invalid versions, and malformed JSON
- deterministic priority and complete precedence behavior
- constant rule query count as import row count grows
- rule ID propagation through preview and commit
- workspace resource isolation
- CRUD, optimistic locking, reorder, enable/disable, and delete
- no-JavaScript, keyboard, mobile, CSRF, and browser flows
- impact and conflict previews
- historical manual protection, selection limits, digest staleness, atomicity, and idempotency
- redacted audit and correction events
- simulator non-mutation and bounded metrics

Final verification requires:

- all Pytest tests passing
- Ruff lint passing
- Ruff formatting check passing
- fresh-database Alembic upgrade to head
- full migration downgrade/upgrade tests
- applicable Playwright browser tests passing

The isolated baseline before implementation is 906 passing tests, Ruff clean, and 247 formatted files.

## Acceptance Criteria

The project is complete when all four stacked branches exist, each PR-sized layer is independently testable, the final branch contains the entire approved capability, current exact rules remain compatible, imports do not query rules per row, every historical write is previewed and confirmed, manual categorizations remain protected, rule behavior is explainable, metrics are privacy-safe, and the complete verification suite passes.
