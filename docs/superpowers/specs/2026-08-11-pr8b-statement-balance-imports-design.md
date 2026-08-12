# PR 8b — Account Statement Balance Imports Design

**Date:** 2026-08-11

**Status:** Approved design; awaiting written-spec review

**Branch base:** merged default branch through PR 7

**Implementation branch:** `codex/pr-8b-statement-balance-imports`

## Purpose

PR 8b lets a workspace member import the total balance from an account statement
without retyping it. It extends PR 7's account-management and manual-balance
workflow; it does not replace either one.

Every extraction result is only a candidate. A member must review editable
account identity, destination account, positive total balance, and as-of date
before the application creates an `account_balance_snapshot`. Until that
confirmation succeeds, dashboard totals remain unchanged.

The feature tracks one total balance per statement. It does not import holdings,
positions, lots, security prices, transactions, payments, or payoff projections.

## Product decisions

Financial institutions do not publish statements in one universal document
schema. Fidelity and Robinhood offer native PDF statements, while mortgage and
loan statement delivery and layout vary by servicer. PR 8b therefore documents
and tests content patterns rather than claiming that every file from a named
institution is supported.

The workflow follows four rules:

1. Accept a native file only when a deterministic category processor finds the
   required values without ambiguity.
2. Reject missing or conflicting values rather than guessing.
3. Keep PR 7's manual balance form as the fallback for an unsupported layout.
4. Never save extracted values without an editable human review.

The first version remains local and deterministic. A future optional AI
extractor may propose candidates after deterministic processing fails, but it
must use the same review and validation boundary and must never write a balance
snapshot directly.

## Supported account categories

The upload form requires a declared statement category. Declaring the category
is not a claim about the document's contents; it selects the narrow processor
and the compatible destination accounts shown during review.

| Statement category | Compatible PR 7 account types | Classification |
| --- | --- | --- |
| 401(k) | `investment_401k` | Asset |
| Brokerage | `investment_brokerage` | Asset |
| Mortgage | `mortgage` | Liability |
| Loan | `auto_loan`, `student_loan` | Liability |
| Other | `other` | Account's saved asset/liability choice |

Checking, savings, and credit-card statement imports remain unavailable. Their
account rows do not show an import action, and the server rejects attempts to
submit them as a statement category. Manual balance entry remains available for
every PR 7 account type.

## Exact supported V1 formats

The accepted extensions are `.csv`, `.pdf`, `.png`, `.jpg`, and `.jpeg`. A file
may be at most 10 MiB. File extension, declared media type, and actual signature
must agree. Encrypted, malformed, empty, oversized, or unsupported files are
rejected safely.

### WIMM balance CSV

CSV support is an explicitly user-prepared interchange format. It is not
advertised as a native Fidelity, Robinhood, mortgage-servicer, or lender export.

The file must be UTF-8 (an optional UTF-8 BOM is accepted), comma-delimited, and
contain exactly this header and one data row:

```csv
account_name,institution,account_last_four,total_balance,as_of_date
Northstar Retirement Plan,Fictional Fidelity,4821,125430.18,2026-07-31
```

Rules:

- `account_name`, `total_balance`, and `as_of_date` are required;
- `institution` and `account_last_four` may be empty;
- `account_last_four`, when present, is exactly four ASCII digits;
- `total_balance` is a non-negative decimal dollar amount with at most two
  decimal places, optional dollar sign, and optional thousands separators;
- `as_of_date` is an ISO `YYYY-MM-DD` calendar date; and
- extra columns, extra rows, formulas, alternate delimiters, and transaction
  exports are rejected.

### PDF and image statements

Text PDFs use embedded text. A text-empty PDF is rendered locally and passed to
local Tesseract OCR. PNG and JPEG images use the same local OCR boundary. The
existing payslip document extractor's page-count, pixel-count, signature, and
rendering protections are reused through a statement-specific adapter; PR 8b
does not refactor that extractor or generic upload storage.

A supported document must produce all three candidate groups below:

1. **Account identity:** at least one unambiguous `Account name`, `Plan name`,
   `Account number`, or `Account ending in` label. Full account numbers are
   reduced to the last four characters before persistence. An optional
   `Institution`, `Provider`, `Servicer`, or issuer heading may also be captured.
2. **Total balance:** exactly one category-compatible total after normalization.
3. **As-of date:** exactly one `As of date`, `Statement date`, or `Period ending`
   value after normalization.

Supported total-balance labels are intentionally narrow:

| Processor | Accepted total labels |
| --- | --- |
| 401(k) | `Total account balance`, `Total plan balance`, `Ending account value`, `Account value` |
| Brokerage | `Total account value`, `Ending account value`, `Net account value`, `Portfolio value` |
| Mortgage | `Unpaid principal balance`, `Current principal balance`, `Remaining principal balance` |
| Loan | `Outstanding principal balance`, `Current principal balance`, `Remaining principal balance` |
| Other | `Total balance`, `Ending balance`, `Current balance` |

Matching is case-insensitive and tolerates whitespace around labels and values.
Dates may be `YYYY-MM-DD`, `MM/DD/YYYY`, or a spelled English month form such as
`July 31, 2026`. Money may contain a dollar sign, commas, and two decimal places.
Parenthesized or negative totals are rejected because PR 7 stores positive
asset values and positive liability amounts owed.

Labels such as `amount due`, `minimum payment`, `payment amount`, `available
cash`, `buying power`, `holdings`, `market change`, and `payoff amount` are never
treated as the account total. If multiple recognized labels yield different
totals, dates, or identities, the processor returns an ambiguity error. Repeated
identical values are allowed.

The documentation will describe these content contracts and limitations. Tests
use fictional documents shaped like common investment, mortgage, and loan
statements; they do not contain real customer data or promise compatibility
with every revision of a provider's layout.

## Architecture and integration boundary

Create `app/statement_imports/` as a feature-owned package:

- `types.py` defines statement categories, immutable candidates, source
  metadata, and processor results;
- `parsing.py` validates the strict CSV and shared date, money, identity, and
  ambiguity rules;
- `processors.py` contains one deterministic processor per supported category;
- `extraction.py` adapts PDF/image bytes to the existing local document
  extraction interface without changing payslip behavior;
- `storage.py` provides the private source-store protocol and current local
  implementation;
- `service.py` coordinates one-file ingestion, pending imports, scoped review,
  confirmation, idempotency, retention cleanup, and account compatibility;
- `routes.py` contains thin authorized upload/review/confirm routes; and
- `body_limit.py` bounds this feature's multipart requests without editing the
  generic upload middleware under active development.

The stable service operation is conceptually:

```text
ingest_one_statement(
    workspace,
    declared_category,
    filename,
    media_type,
    stream,
    retention_choice,
) -> pending statement import
```

The current server-rendered route calls it once. The separately developed
multi-file drag-and-drop uploader can call it once per accepted file and collect
the resulting pending imports. PR 8b will not assume a batch payload, JavaScript
event shape, temporary-file type, or generic uploader implementation that has
not landed. The exact later integration point is this operation or a small
adapter around it.

This boundary also prepares for future extraction strategies. A future AI
candidate extractor can implement the candidate-extraction protocol, but all
workspace lookup, compatible-account selection, review validation,
confirmation, and snapshot creation remain in the deterministic service.

## Persistence and migration

Add an `account_statement_imports` table with:

- `id`;
- `workspace_id`;
- `uploaded_file_id`;
- nullable `account_id`, set only when confirmation chooses a destination;
- `statement_category`;
- `source_checksum`;
- `candidate_fields` JSON;
- nullable `confirmed_fields` JSON;
- `review_status` (`pending`, `confirmed`, or
  `confirmed_cleanup_failed`); and
- created/updated timestamps.

A workspace-level unique constraint on
`(workspace_id, statement_category, source_checksum)` makes an exact re-upload
under the same processor resume or return its existing pending/confirmed
import. A user who selected the wrong processor may retry the same source under
the correct category. V1 supports one account total per source file;
consolidated multi-account statements are rejected as ambiguous.

Add nullable `statement_import_id` to `account_balance_snapshots` with a unique
index and foreign key. A statement-confirmed snapshot also carries:

- the trusted workspace and account IDs;
- the reviewed positive `balance_cents` and `as_of_date`;
- `source="statement_import"`; and
- the import's `uploaded_file_id`.

The unique snapshot link is the database backstop for idempotent or concurrent
confirmation. Existing manual snapshots and their `source="manual"` behavior do
not change.

`UploadedFile.file_type` is `account_statement`. The source may be retained
privately or deleted after successful confirmation, matching the existing
payslip policy. Failed extraction removes the stored source and creates no
database rows. Pending imports retain the source so review can be audited and
retried.

## Data flow

### Ingestion

1. Workspace authorization and CSRF validation run before feature logic.
2. The route or future multi-file adapter supplies one file and declared
   category to `ingest_one_statement`.
3. Storage streams to an opaque workspace-prefixed key while hashing and
   enforcing the size limit.
4. The parser validates CSV directly or obtains local PDF/image text through
   the extraction adapter.
5. The declared category processor extracts an identity, total, and date. Any
   missing or conflicting required value rejects the import.
6. One `UploadedFile` and one pending `AccountStatementImport` are committed
   together. No snapshot is created.
7. The user is sent to the editable review page.

### Review and confirmation

The review page displays the extraction method, declared category, editable
identity fields, a destination-account selector filtered to compatible accounts
in the active workspace, positive total balance, and as-of date. Candidate
identity is review evidence; it does not silently rename or edit the PR 7
account.

On confirmation, the service reloads the import through both import ID and
workspace ID, validates every submitted field independently of extraction,
reloads the selected account through workspace ID, checks category
compatibility, and creates the linked snapshot in one transaction. The import's
`confirmed_fields` records the reviewed identity and values. A repeated confirm
returns the existing snapshot. The reviewed date must be a real calendar date
no later than the current UTC date, matching PR 7 manual-balance behavior.

If retained-source cleanup fails after confirmation, the snapshot remains
committed, the import records `confirmed_cleanup_failed`, and the user sees a
safe retry notice. Cleanup never rolls back a valid confirmed balance.

The successful flow redirects to the existing PR 7 dashboard. The dashboard
already selects the newest eligible snapshot per account, so no balance, net
worth, cash, or trend calculation is duplicated or changed.

## Error handling and privacy

- Missing and foreign import/account IDs return the same 404 response.
- Candidate fields and uploaded content are never written to logs.
- Full account numbers are not persisted; only a last-four hint is retained.
- OCR and PDF parsing run locally with no network calls.
- Templates use Jinja escaping, and every mutation uses the existing CSRF
  dependency.
- Future hosted-AI extraction requires a separate product and privacy design,
  explicit user consent, provider configuration, redacted audit metadata, and
  adversarial document tests. It is not part of PR 8b.
- A document's text is always untrusted data. Neither the deterministic parser
  nor any future AI extractor may interpret document text as application
  instructions.

## UI behavior

The Accounts page keeps `Add balance` for every account. Supported account rows
also show `Import statement`. The upload page explains supported files, total
balance only, local processing, retention choice, and manual-entry fallback.

The review page makes extracted values visually distinct from confirmed data
and states that dashboard totals will not change until confirmation. If no
compatible account exists, it links to PR 7 account creation and does not offer
confirmation. Unsupported categories never display an enabled upload control.

All important values and errors remain server-rendered HTML. No JavaScript is
required to complete a single-file upload or review; the separate drag-and-drop
enhancement can progressively enhance the upload step.

## Test strategy

Use only fictional institutions, accounts, numbers, and statement content.

### Parser and processor tests

- exact accepted and rejected CSV shapes;
- each documented identity, date, and category-specific total label;
- local embedded-PDF, scanned-PDF, PNG, and JPEG paths;
- conflicting totals/dates/identities and excluded payment/holdings labels;
- malformed, encrypted, oversized, excessive-page, excessive-pixel, and
  unsupported files; and
- identical deterministic results across repeated runs.

### Service and persistence tests

- no snapshot and no dashboard-total change before confirmation;
- reviewed edits, positive money/date boundaries, and compatible accounts;
- workspace-scoped import, account, file, and snapshot writes;
- exact re-upload and repeated/concurrent confirmation idempotency;
- retained and deleted source behavior, including cleanup failure; and
- migration upgrade/downgrade plus model relationship round trips.

### Route and acceptance tests

- authentication, membership, CSRF, and foreign-resource 404 behavior;
- supported controls and server rejection for unavailable categories;
- upload errors that do not leak candidate or foreign account data;
- editable review and confirmation through real routes; and
- synthetic 401(k), brokerage, mortgage, loan, and other statements whose
  confirmed snapshots update the existing PR 7 dashboard to exact asset,
  liability, and net-worth totals.

Run focused statement-import tests, existing account/dashboard tests, the full
Pytest suite, Ruff lint, Ruff format check, and a fresh Alembic migration to
head.

## Documentation

Update README and the roadmap to explain:

- supported categories and exact V1 formats;
- how to download or scan a statement without claiming universal provider
  compatibility;
- local extraction and OCR requirements;
- review-before-save and source-retention behavior;
- the manual-balance fallback;
- total balances only, not holdings; and
- the per-file integration point for the later multi-file uploader.

## Explicit non-goals

- generic or multi-file upload refactoring;
- transaction CSV changes;
- payslip or income behavior changes;
- checking, savings, or credit-card balance processors;
- holdings, lots, security prices, performance, or market data;
- budgets, savings goals, bank connections, or money movement;
- LangGraph, an LLM, or any AI extraction implementation; and
- automatic account creation, account renaming, or snapshot confirmation.

## Completion criteria

PR 8b is complete when all five declared categories have deterministic,
documented processors; supported synthetic CSV/PDF/image examples produce only
pending candidates before review; explicit confirmation creates exactly one
workspace-scoped snapshot; unsupported categories never claim support; the PR 7
dashboard reflects confirmed snapshots through its existing calculations; the
multi-file integration boundary is documented without shared-upload
refactoring; and all focused, full, lint, formatting, and migration checks pass.
