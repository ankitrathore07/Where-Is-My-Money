# PR 8b Statement Balance Imports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import reviewed total balances from deterministic local CSV/PDF/image statement processors into PR 7 account snapshots.

**Architecture:** A feature-owned `app/statement_imports/` package ingests one file at a time, normalizes every successful processor into an immutable candidate, persists a pending workspace-scoped review, and creates one idempotently linked PR 7 snapshot only after confirmation. Routes and storage remain single-file adapters so the separately developed multi-file uploader can call the stable ingestion service without PR 8b refactoring generic upload code.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Jinja, pypdf/Pillow/pypdfium2, local Tesseract OCR, Pytest, Ruff.

## Global Constraints

- Supported categories are 401(k), brokerage, mortgage, loan, and other; checking, savings, and credit-card imports remain unavailable.
- Accepted extensions are `.csv`, `.pdf`, `.png`, `.jpg`, and `.jpeg`, with a 10 MiB maximum.
- Extraction is local and deterministic; no network, LangGraph, LLM, holdings, transactions, or provider connection is added.
- No `account_balance_snapshot` is created before editable confirmation.
- Confirmed balances are non-negative integer cents, and confirmed dates are not later than the current UTC date.
- Generic and multi-file upload refactoring is out of scope; integration occurs through `ingest_one_statement(...)`.
- Existing PR 7 account creation, manual balance entry, dashboard queries, and net-worth calculations are reused unchanged.

---

## File structure

- `app/statement_imports/types.py`: statement category catalog, immutable candidates, review commands, and compatibility helpers.
- `app/statement_imports/parsing.py`: strict WIMM CSV parsing plus shared labeled text, date, money, identity, ambiguity, and review validation.
- `app/statement_imports/processors.py`: category-specific total-label selection.
- `app/statement_imports/extraction.py`: statement-facing adapter over the existing local PDF/image extractor.
- `app/statement_imports/storage.py`: private statement source storage with CSV/PDF/image support.
- `app/statement_imports/service.py`: one-file ingestion, duplicate resume, scoped reads, confirmation, idempotency, and cleanup.
- `app/statement_imports/routes.py`: authorized upload, review, and confirmation pages.
- `app/statement_imports/body_limit.py`: route-specific multipart limit without modifying generic upload implementation.
- `app/templates/statement_imports/upload.html`: documented input contract.
- `app/templates/statement_imports/review.html`: editable confirmation.
- `migrations/versions/0009_account_statement_imports.py`: pending import and unique snapshot linkage schema.
- `tests/statement_imports/`: parser, processor, storage, extraction, service, route, migration, and acceptance coverage.

### Task 1: Persistence model and migration

**Files:**
- Modify: `app/db/models.py`
- Create: `migrations/versions/0009_account_statement_imports.py`
- Create: `tests/statement_imports/__init__.py`
- Create: `tests/statement_imports/test_migration.py`
- Create: `tests/statement_imports/test_models.py`

**Interfaces:**
- Produces: `AccountStatementImport` with `workspace_id`, `uploaded_file_id`, nullable `account_id`, `statement_category`, `source_checksum`, `candidate_fields`, nullable `confirmed_fields`, `review_status`, and timestamps.
- Produces: nullable unique `AccountBalanceSnapshot.statement_import_id` and bidirectional relationships.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_statement_import_round_trips_with_pending_candidates(session, workspace):
    uploaded = UploadedFile(workspace_id=workspace.id, file_type="account_statement", storage_path="1/a.csv", checksum="a" * 64, size_bytes=10)
    pending = AccountStatementImport(workspace_id=workspace.id, uploaded_file=uploaded, statement_category="brokerage", source_checksum="a" * 64, candidate_fields={"balance_cents": 12500}, review_status="pending")
    session.add(pending)
    session.commit()
    assert pending.uploaded_file.workspace_id == workspace.id
    assert pending.candidate_fields["balance_cents"] == 12500
```

Migration coverage upgrades a fresh SQLite database through `0009_account_statement_imports`, asserts the new table/columns/indexes, writes a linked import and snapshot, verifies duplicate `(workspace_id, statement_category, source_checksum)` and duplicate `statement_import_id` fail, then downgrades to `0008_unique_payslip_income`.

- [ ] **Step 2: Run tests and verify missing model/migration failures**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_models.py tests/statement_imports/test_migration.py -v`

- [ ] **Step 3: Add the SQLAlchemy model and Alembic revision**

Use a three-column unique constraint named `uix_statement_import_workspace_category_checksum`, a unique index named `uix_balance_snapshot_statement_import_id`, and foreign keys to workspaces, uploaded files, accounts, and statement imports. Add relationships on `Workspace`, `UploadedFile`, `Account`, and `AccountBalanceSnapshot` without changing existing relationship behavior.

- [ ] **Step 4: Run focused model/migration tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_models.py tests/statement_imports/test_migration.py -v`

- [ ] **Step 5: Commit the persistence slice**

```powershell
git add app/db/models.py migrations/versions/0009_account_statement_imports.py tests/statement_imports
git commit -m "feat: add statement import persistence"
```

### Task 2: Format catalog, strict CSV, and deterministic processors

**Files:**
- Create: `app/statement_imports/__init__.py`
- Create: `app/statement_imports/types.py`
- Create: `app/statement_imports/parsing.py`
- Create: `app/statement_imports/processors.py`
- Create: `tests/statement_imports/test_parsing.py`
- Create: `tests/statement_imports/test_processors.py`

**Interfaces:**
- Produces: `StatementCandidate(account_name: str, institution: str | None, account_last_four: str | None, balance_cents: int, as_of_date: date, extraction_method: str)` with `to_json()`.
- Produces: `StatementFormatError(code: str, message: str)`.
- Produces: `parse_wimm_csv(data: bytes) -> StatementCandidate`.
- Produces: `process_statement_text(category: str, text: str, method: str) -> StatementCandidate`.
- Produces: `compatible_account_types(category: str) -> frozenset[str]` and `SUPPORTED_STATEMENT_CATEGORIES`.

- [ ] **Step 1: Write failing catalog and CSV tests**

Cover exact header/order and one row, optional UTF-8 BOM, required name/balance/date, optional institution/last four, exact four ASCII digits, non-negative money with at most two decimals, ISO dates, and rejection of extra rows/columns/formulas/transaction exports.

```python
candidate = parse_wimm_csv(b"account_name,institution,account_last_four,total_balance,as_of_date\nNorthstar Brokerage,Fictional Brokerage,4821,125430.18,2026-07-31\n")
assert candidate.balance_cents == 12_543_018
assert candidate.as_of_date == date(2026, 7, 31)
assert candidate.extraction_method == "wimm_csv"
```

- [ ] **Step 2: Run CSV tests and verify imports fail**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_parsing.py -v`

- [ ] **Step 3: Implement minimal immutable types and strict CSV parser**

Use Python's `csv.DictReader`, `Decimal`, exact header comparison, and shared bounded normalization. Candidate account names and institutions are at most 255 characters; balance maximum is PR 7's `MAX_BALANCE_CENTS`.

- [ ] **Step 4: Write failing labeled-document processor tests**

Parameterize every label from the design. Assert case/whitespace tolerance, ISO/US/spelled-month dates, full account-number reduction to last four, repeated-identical values, and rejection of conflicting totals/dates/identities, negative/parenthesized totals, payment amounts, buying power, holdings, and consolidated multi-account statements.

```python
candidate = process_statement_text("mortgage", "Servicer: Northstar Home Loans\nAccount ending in 7742\nStatement date: July 31, 2026\nUnpaid principal balance: $248,125.44", "embedded_text")
assert candidate.balance_cents == 24_812_544
assert candidate.account_last_four == "7742"
```

- [ ] **Step 5: Implement category-specific processors**

Keep label sets in a category-keyed constant and use shared full-line label extraction. Require identity, one normalized total, and one normalized date. `other` accepts only its documented generic balance labels.

- [ ] **Step 6: Run all parser/processor tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_parsing.py tests/statement_imports/test_processors.py -v`

- [ ] **Step 7: Commit deterministic processors**

```powershell
git add app/statement_imports tests/statement_imports/test_parsing.py tests/statement_imports/test_processors.py
git commit -m "feat: parse supported statement balances"
```

### Task 3: Private storage and local document extraction adapter

**Files:**
- Create: `app/statement_imports/storage.py`
- Create: `app/statement_imports/extraction.py`
- Create: `tests/statement_imports/test_storage.py`
- Create: `tests/statement_imports/test_extraction.py`

**Interfaces:**
- Produces: `StoredStatementUpload(storage_key: str, checksum: str, size_bytes: int)`.
- Produces: `StatementUploadStore.save(workspace_id: int, suffix: str, stream: BinaryIO)`, `.read(storage_key)`, and `.delete(storage_key)`.
- Produces: `StatementDocumentExtractor.extract(data: bytes, suffix: str) -> ExtractedText`, delegating PDF/images to an injected `DocumentExtractor` while translating user-facing payslip wording to statement wording.

- [ ] **Step 1: Write failing storage tests**

Cover opaque workspace keys for all five extensions, `.jpeg` canonicalization to `.jpg`, hashing, 10 MiB bound, partial cleanup, traversal rejection, safe missing reads, and idempotent deletion.

- [ ] **Step 2: Run storage tests and verify failure**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_storage.py -v`

- [ ] **Step 3: Implement feature-local statement storage**

Mirror the proven private-storage mechanics without modifying `app/imports/storage.py` or `app/payslips/storage.py`; use the key pattern `^[1-9]\d*/[0-9a-f]{32}\.(?:csv|pdf|png|jpg)$`.

- [ ] **Step 4: Write failing extraction adapter tests**

Assert CSV bypass is rejected by the document adapter, embedded PDF and injected OCR results pass through, and every raised message says statement rather than payslip while preserving error codes and safety limits.

- [ ] **Step 5: Implement the extraction adapter**

Inject `DocumentExtractor`; catch `DocumentExtractionError` and raise a new error with the same code and `message.replace("payslip", "statement").replace("Payslip", "Statement")`.

- [ ] **Step 6: Run storage/extraction and existing payslip tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_storage.py tests/statement_imports/test_extraction.py tests/payslips/test_extraction.py -v`

- [ ] **Step 7: Commit source handling**

```powershell
git add app/statement_imports/storage.py app/statement_imports/extraction.py tests/statement_imports/test_storage.py tests/statement_imports/test_extraction.py
git commit -m "feat: store and extract statement sources locally"
```

### Task 4: One-file ingestion service

**Files:**
- Create: `app/statement_imports/service.py`
- Create: `tests/statement_imports/test_service_ingestion.py`

**Interfaces:**
- Produces: `ingest_one_statement(session, store, extractor, workspace, declared_category, filename, media_type, stream, retention_choice) -> AccountStatementImport`.
- Produces: `get_workspace_statement_import(session, workspace_id, statement_import_id) -> AccountStatementImport | None`.
- Consumes: parser, processor, storage, extraction, and Task 1 model.

- [ ] **Step 1: Write failing ingestion tests**

Cover valid CSV and extracted document creation, actual/declared type validation, retention validation before storage, safe cleanup on extraction/parsing failure, uploaded-file metadata, no snapshot before confirmation, workspace/category/checksum duplicate resume, same bytes under a corrected category, and the explicit one-file stream interface.

```python
pending = ingest_one_statement(session, store, extractor, workspace, "brokerage", "statement.csv", "text/csv", BytesIO(csv_bytes), "retain")
assert pending.review_status == "pending"
assert session.query(AccountBalanceSnapshot).count() == 0
```

- [ ] **Step 2: Run ingestion tests and verify missing service failure**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_service_ingestion.py -v`

- [ ] **Step 3: Implement ingestion transaction and duplicate resume**

Validate suffix/media/category/retention, store and hash, check the scoped three-column duplicate key, parse CSV bytes or extract/process document text, create `UploadedFile(file_type="account_statement")` plus the pending import, commit once, and delete newly stored duplicate/failed files. Never create or call the account snapshot service here.

- [ ] **Step 4: Run focused ingestion tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_service_ingestion.py -v`

- [ ] **Step 5: Commit one-file ingestion**

```powershell
git add app/statement_imports/service.py tests/statement_imports/test_service_ingestion.py
git commit -m "feat: ingest one statement for review"
```

### Task 5: Editable review validation and idempotent confirmation

**Files:**
- Modify: `app/statement_imports/types.py`
- Modify: `app/statement_imports/parsing.py`
- Modify: `app/statement_imports/service.py`
- Create: `tests/statement_imports/test_confirmation.py`

**Interfaces:**
- Produces: `validate_statement_review(form: Mapping[str, str], *, today: date) -> StatementReviewValues`.
- Produces: `list_compatible_accounts(session, workspace_id, category) -> tuple[Account, ...]`.
- Produces: `confirm_statement_import(session, store, pending, form, *, today: date) -> StatementConfirmationResult`.

- [ ] **Step 1: Write failing review validation tests**

Cover normalized name/institution, optional exact last four, account ID, positive money parsing up to PR 7 maximum, ISO real dates through `today`, and field-specific errors.

- [ ] **Step 2: Write failing confirmation tests**

Cover compatible 401(k)/brokerage/mortgage/loan/other accounts, foreign/incompatible accounts, exact reviewed edits, `source="statement_import"`, uploaded/import linkage, confirmed fields, repeated confirmation, unique-link concurrency recovery, source retain/delete behavior, and cleanup-failure state.

```python
result = confirm_statement_import(session, store, pending, {"account_id": str(account.id), "account_name": "Reviewed", "institution": "Northstar", "account_last_four": "4821", "total_balance": "125430.18", "as_of_date": "2026-07-31"}, today=date(2026, 8, 11))
assert result.snapshot.balance_cents == 12_543_018
assert result.snapshot.source == "statement_import"
```

- [ ] **Step 3: Run confirmation tests and verify failure**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_confirmation.py -v`

- [ ] **Step 4: Implement review validation and confirmation**

Reload the selected account through both ID and workspace ID, enforce category compatibility and fixed classification, create snapshot/import updates in one commit, recover the existing linked snapshot after `IntegrityError`, then perform optional source cleanup in a second commit.

- [ ] **Step 5: Run confirmation plus PR 7 account/dashboard tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_confirmation.py tests/accounts tests/dashboard -v`

- [ ] **Step 6: Commit confirmation workflow**

```powershell
git add app/statement_imports tests/statement_imports/test_confirmation.py
git commit -m "feat: confirm reviewed statement balances"
```

### Task 6: Authorized server-rendered routes and bounded upload

**Files:**
- Create: `app/statement_imports/routes.py`
- Create: `app/statement_imports/body_limit.py`
- Create: `app/templates/statement_imports/upload.html`
- Create: `app/templates/statement_imports/review.html`
- Modify: `app/main.py`
- Modify: `app/templates/accounts/index.html`
- Modify: `tests/route_helpers.py`
- Create: `tests/statement_imports/test_routes.py`

**Interfaces:**
- Produces: `GET /workspaces/{workspace_id}/accounts/{account_id}/statements/new`.
- Produces: `POST /workspaces/{workspace_id}/statement-imports`.
- Produces: `GET /workspaces/{workspace_id}/statement-imports/{id}/review`.
- Produces: `POST /workspaces/{workspace_id}/statement-imports/{id}/confirm`.

- [ ] **Step 1: Write failing route and authorization tests**

Cover unauthenticated redirects, nonmember/foreign 404s, CSRF, upload content types, server rejection for checking/savings/credit-card accounts and categories, compatible account selector isolation, editable error redisplay, pre-confirmation dashboard totals, confirmation redirect, cleanup warning, and enabled import links only for supported account types.

- [ ] **Step 2: Write failing body-limit tests**

Assert only the exact statement-import POST path is bounded, with and without `Content-Length`, and that unrelated generic upload routes are untouched.

- [ ] **Step 3: Run route tests and verify missing route failure**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_routes.py -v`

- [ ] **Step 4: Implement feature routes, templates, and middleware**

Derive the category from the trusted account selected on the GET page, but validate the posted category again. Wire `StatementUploadStore` and `StatementDocumentExtractor` through application state for tests. Add only narrow imports/state/middleware/router lines to `app/main.py`; do not edit generic upload modules.

- [ ] **Step 5: Run routes and shared navigation regression tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_routes.py tests/test_shared_navigation.py tests/accounts/test_routes.py -v`

- [ ] **Step 6: Commit the web workflow**

```powershell
git add app/main.py app/statement_imports/routes.py app/statement_imports/body_limit.py app/templates/statement_imports app/templates/accounts/index.html tests/route_helpers.py tests/statement_imports/test_routes.py
git commit -m "feat: add statement balance review routes"
```

### Task 7: End-to-end dashboard acceptance and synthetic fixtures

**Files:**
- Create: `tests/fixtures/statements/synthetic_401k.csv`
- Create: `tests/fixtures/statements/synthetic_brokerage.txt`
- Create: `tests/fixtures/statements/synthetic_mortgage.txt`
- Create: `tests/fixtures/statements/synthetic_loan.txt`
- Create: `tests/fixtures/statements/synthetic_other.txt`
- Create: `tests/statement_imports/test_acceptance.py`

**Interfaces:**
- Consumes: real ingestion, review confirmation, and existing `build_dashboard_report` behavior.
- Produces: proof that confirmed statement snapshots update PR 7 without dashboard code changes.

- [ ] **Step 1: Add fictional supported fixtures and failing acceptance test**

Create five files using only documented labels and values. Ingest all five, assert dashboard totals are unchanged, confirm them into matching accounts, then assert exact assets, liabilities, net worth, and account positions from the existing dashboard service. Also assert unsupported account types expose no route and holdings text never becomes a balance.

- [ ] **Step 2: Run acceptance test and verify any integration failures**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports/test_acceptance.py -v`

- [ ] **Step 3: Make only minimal integration corrections**

Correct statement-import boundaries or fixture content; do not modify `app/dashboard/service.py`, duplicate dashboard arithmetic, or add provider-specific guesses.

- [ ] **Step 4: Run all statement, account, and dashboard tests**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports tests/accounts tests/dashboard -v`

- [ ] **Step 5: Commit acceptance coverage**

```powershell
git add tests/fixtures/statements tests/statement_imports/test_acceptance.py
git commit -m "test: verify confirmed statements refresh dashboard"
```

### Task 8: Documentation, roadmap status, and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Modify: `docs/superpowers/plans/2026-08-11-pr8b-statement-balance-imports.md`

**Interfaces:**
- Produces: exact user-facing V1 format documentation and multi-file integration note.

- [ ] **Step 1: Update documentation**

Document supported/unavailable categories, exact CSV header/one-row rule, PDF/image labels, local OCR, editable confirmation, retention, manual fallback, total-balances-only scope, non-universal provider compatibility, and `ingest_one_statement(...)` as the future multi-file integration point. Mark PR 8b implemented only after verification passes.

- [ ] **Step 2: Run formatting and lint**

Run: `uv run ruff format .`

Run: `uv run ruff check .`

Run: `uv run ruff format --check .`

- [ ] **Step 3: Run focused verification**

Run: `uv run pytest --basetemp .test-tmp tests/statement_imports tests/accounts tests/dashboard -q`

- [ ] **Step 4: Run full verification**

Run: `uv run pytest --basetemp .test-tmp -q`

Run a fresh migration with a workspace-local SQLite URL:

```powershell
$env:DATABASE_URL = 'sqlite:///./.migration-check.db'
uv run alembic upgrade head
uv run alembic downgrade 0008_unique_payslip_income
uv run alembic upgrade head
```

Delete only the verified workspace-local `.test-tmp` and `.migration-check.db` artifacts afterward.

- [ ] **Step 5: Review the diff**

Run: `git diff --check origin/main...HEAD`

Run: `git diff --stat origin/main...HEAD`

Run: `git status --short --branch`

Confirm no generic upload refactor, dashboard calculation rewrite, transaction import change, payslip behavior change, holdings support, AI implementation, or unrelated user change is present.

- [ ] **Step 6: Commit verified docs and final corrections**

```powershell
git add README.md docs/where-is-my-money-pr-breakdown.md docs/superpowers/plans/2026-08-11-pr8b-statement-balance-imports.md
git commit -m "docs: explain statement balance imports"
```

- [ ] **Step 7: Prepare the PR-ready handoff**

Report branch, commits, exact checks and results, supported formats/categories, multi-file integration point, migration, security/privacy behavior, manual fallback, and remaining limitations. Do not merge.
