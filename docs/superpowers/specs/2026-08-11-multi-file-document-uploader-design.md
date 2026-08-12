# Multi-File Document Uploader Design

## Goal

Add one workspace-scoped document upload page that supports clicking to browse or
dragging and dropping multiple files, requires the user to choose a document
category for every file, and routes supported files into the existing transaction
statement or payslip workflows only after an explicit review action.

V1 does not classify documents automatically. AI/ML category suggestions and a
category-administration interface are documented future enhancements with their
own design and security decisions.

## Scope and constraints

- Present one compact review table for files added through the native file picker
  or drag and drop.
- Accept CSV, PDF, PNG, and JPEG files. The browser queue holds at most 10 files at
  once.
- Keep every file in browser memory until the user clicks `Process`.
- Require an explicit category selection for every file. V1 does not infer,
  preselect, or silently change a category.
- Process transaction statements only when they are CSV files, using the existing
  CSV import service and its 5 MiB per-file limit.
- Process payslips only when they are PDF, PNG, or JPEG files, using the existing
  payslip service and its 10 MiB per-file limit.
- Recognize future document categories in the interface without accepting or
  storing their files on the server.
- Upload eligible files sequentially so several large PDFs or OCR jobs cannot run
  concurrently from one queue.
- Preserve the current private-storage, review-before-commit, workspace-access,
  CSRF, retention, and safe-error guarantees.
- Keep the specialized CSV and payslip upload pages available as compatibility and
  non-JavaScript fallbacks. Workspace navigation points to the unified page.
- Add no database table or migration for upload batches or document categories in
  V1.
- Use synthetic documents in all tests. Never log source content, extracted text,
  or financial values.

## Document category catalog

V1 defines the following stable category keys in code:

| Key | User-facing label | Accepted format for processing | Processor |
| --- | --- | --- | --- |
| `transaction_statement` | Bank or credit-card transaction statement | CSV | Existing CSV import workflow |
| `payslip` | Payslip | PDF, PNG, JPEG | Existing payslip workflow |
| `retirement_401k_statement` | 401(k) retirement statement | None in V1 | Not available yet |
| `brokerage_statement` | Brokerage or stocks statement | None in V1 | Not available yet |
| `mortgage_statement` | Mortgage statement | None in V1 | Not available yet |
| `loan_statement` | Loan statement | None in V1 | Not available yet |
| `other_account_statement` | Other account statement | None in V1 | Not available yet |
| `unlisted` | Category not listed | None | Not available |

Each category definition has a stable key, display label, accepted technical file
formats, and optional processor key. Processor assignment remains code-controlled.
The catalog is not stored in or editable through the database in V1.

Choosing a category without a processor shows `Recognized, but processing is not
available yet`. Choosing `Category not listed` shows `Remove this file or choose a
supported category`. Neither kind of row is sent to the server.

## User experience

### Entry point and drop zone

The workspace page replaces its separate primary import buttons with one `Upload
documents` action. The unified page explains that files remain local until the
member reviews the queue and starts processing.

The drop zone is a real button or label associated with a visually hidden native
`<input type="file" multiple>`. It accepts click, keyboard activation, and drag and
drop. Dragging files over the target provides a visible state without disabling
the native focus indicator. Browse selections and drops append to the same queue.

The global accepted extensions are `.csv`, `.pdf`, `.png`, `.jpg`, and `.jpeg`.
Files outside those formats receive a local row error and are not eligible for
upload. Re-adding the same browser file signature (name, size, last-modified time,
and declared type) does not create a second row in the current queue; an accessible
status message explains that it is already present.

### Compact review table

Each queued file receives a stable client-generated row identifier and displays:

- filename and human-readable size;
- a required native document-category dropdown that initially reads `Choose a
  category`;
- a readiness or error message;
- an `X` button with an accessible name such as `Remove fidelity-401k.pdf`;
- after successful processing, a server-provided next-step link such as `Map
  columns` or `Review payslip`.

The browser renders filenames with `textContent`, never HTML. At narrow widths the
table rows reflow into stacked file summaries rather than forcing the page to
shrink or clip controls.

Selecting or changing a category immediately recalculates the row state:

- a transaction statement paired with CSV becomes ready;
- a payslip paired with PDF, PNG, or JPEG becomes ready;
- a supported category paired with the wrong format gets an actionable mismatch
  error;
- a future or unlisted category remains local and ineligible;
- changing a category after a validation failure resets the row for another
  attempt.

Removing a pending row releases the browser reference to its `File` object and
removes it from processing. A completed row is locked and replaces removal with
its next-step link.

### Retention and processing controls

One batch-wide retention choice applies to all eligible rows:

- `Delete each raw document after successful completion` is selected by default.
- `Retain each raw document privately` is the alternative.

The existing service semantics remain unchanged: a transaction CSV is eligible
for deletion after import commit, while a payslip source is eligible for deletion
after income confirmation.

The primary button includes the ready count, for example `Process 3 files`, and is
disabled when no pending row is eligible. Unsupported and unlisted rows do not
block eligible rows from processing. The user can reclassify or remove them.

## Architecture

Create a focused `app/documents/` package that coordinates existing features
without absorbing their parsing, storage, or business rules:

- `catalog.py` defines immutable document category values and performs category /
  format compatibility checks.
- `routes.py` renders the unified page and exposes a one-file processing endpoint.
- `types.py` defines the small processor result contract returned to the route.
- `app/templates/documents/upload.html` contains the progressive server-rendered
  shell, semantic queue table, retention controls, and fallback links.
- `app/static/document-upload.js` owns only client queue state, drag/drop behavior,
  local compatibility feedback, sequential requests, and row updates.
- `app/static/styles.css` adds the compact table, responsive row, drop-target, and
  status styles using the existing visual language.

The application factory registers the document router. It continues to provide
the existing CSV and payslip stores and payslip extractor through application
state. The document route delegates to the current `create_csv_import` and
`create_payslip_import` services instead of duplicating their logic.

No V1 code attempts to identify a document from its content, filename, metadata,
CSV headers, embedded PDF text, or OCR output. File-content inspection occurs only
inside the existing processor selected explicitly by the user.

## HTTP contract and data flow

### Page request

`GET /workspaces/{workspace_id}/documents/new` requires an authenticated member of
the workspace and renders the unified uploader with the code-defined category
catalog, CSRF token, per-format size limits, and fallback links.

### Per-file processing request

After the user clicks `Process`, JavaScript visits ready rows in queue order and
sends one multipart request at a time to:

`POST /workspaces/{workspace_id}/document-uploads`

Each request contains exactly one `document`, one `category_key`, the batch-wide
`retention_choice`, and the CSRF token. The endpoint rejects a request containing
an unknown, unlisted, or processor-less category before calling a storage service.

For a supported category, the endpoint:

1. Rechecks authentication, workspace membership, CSRF, category key, extension,
   declared content type, and route-level request size.
2. Delegates a transaction statement to `create_csv_import` or a payslip to
   `create_payslip_import`.
3. Relies on the delegated parser or extractor to validate the actual bytes,
   preserve its existing safety limits, create the pending database record, and
   clean up partial storage on failure.
4. Returns a same-origin, server-generated next-step URL on success or a safe,
   actionable error on failure.

The JSON success shape is:

```json
{
  "ok": true,
  "message": "Ready for review.",
  "next_url": "/workspaces/7/imports/42/mapping",
  "next_label": "Map columns"
}
```

The JSON error shape is:

```json
{
  "ok": false,
  "code": "category_format_mismatch",
  "message": "Transaction statements must be CSV files."
}
```

The browser treats `next_url` as an anchor destination and never navigates
automatically. It updates the completed row, then processes the next ready row.
One file's validation, extraction, storage, or network failure does not roll back
or suppress other file results.

## Error handling

- Unsupported extension: keep the row local, explain the accepted formats, and
  allow removal.
- Missing category: keep the row local and prompt for a category.
- Format/category mismatch: keep the row local and explain the required format.
- Future or unlisted category: keep the row local and state that processing is not
  available.
- File too large: show the category-specific limit locally when possible; the
  server independently enforces the limit and returns the same safe result.
- Invalid signature, malformed CSV/PDF/image, unavailable OCR, or extraction
  failure: preserve the existing service error and partial-file cleanup behavior,
  then show the safe message beside that row.
- Network or transient server failure: mark only that row as retryable and
  continue with later ready rows. Retry resends only the failed file.
- Authentication or CSRF expiration: stop starting new requests, preserve the
  local queue, and show a page-level prompt to reload or sign in. Already completed
  rows remain truthful.

Server responses never echo source bytes, extracted text, or native OCR/parser
diagnostics. Client errors do not include file contents.

## Security and privacy

- Every GET and POST remains workspace-scoped through the existing dependencies.
- The per-file endpoint requires the existing CSRF protection and rejects foreign
  workspaces with the same behavior as the specialized routes.
- The route-level multipart limit bounds the request before form parsing; the CSV
  and payslip stores retain their stricter per-format limits.
- Category keys are allow-listed on the server. Client readiness state is never
  trusted for routing or validation.
- Unsupported, unlisted, and mismatched files do not reach storage or document
  extraction.
- The endpoint supplies `next_url`; the client does not construct identifiers or
  cross-workspace paths.
- Uploaded filenames are displayed as text, are not used as storage paths, and are
  not written to logs.
- Processing stays local to the application machine. V1 introduces no external
  document or model service.

## Accessibility

- The native multiple-file input and category selects remain in the accessibility
  tree.
- The drop zone is keyboard operable and does not replace the native picker.
- Every remove action includes the filename in its accessible label.
- Row and batch status changes are announced through a polite live region; session
  or CSRF failures use an assertive page-level alert.
- Status does not rely on color alone. Text distinguishes ready, unsupported,
  invalid, processing, completed, and retryable states.
- Focus moves predictably: adding files announces the count without stealing
  focus, removing a row returns focus to the next logical control, and processing
  does not navigate away from the queue.

## Testing strategy

Use red-green-refactor cycles and synthetic fixtures for each behavior.

- Catalog tests: stable keys and labels, supported processors, accepted format
  combinations, unknown keys, processor-less categories, and mismatch messages.
- Route tests: authentication, CSRF, workspace isolation, one-file requirement,
  category allow-listing, unsupported categories causing no storage/database
  writes, extension and content-type checks, retention propagation, safe errors,
  and exact success JSON for both delegated workflows.
- Service integration tests: a CSV response points to mapping, a text PDF response
  points to payslip review, malformed sources clean up safely, and existing
  duplicate/retention behavior remains unchanged.
- Playwright interaction tests: queue append and same-file suppression, initial
  manual category state, compatibility calculation, ready counts, removal,
  category changes, sequential request ordering, partial success, retry, and
  session/CSRF stop behavior.
- Playwright browser-flow test: add multiple files by picker and drag/drop, select
  and edit categories, remove a row with `X`, verify unsupported rows are not
  requested, process a mixed success/failure queue, and follow the resulting
  mapping and review links.
- Regression gates: existing CSV and payslip suites, Ruff lint, Ruff format check,
  the complete Pytest suite, a fresh Alembic upgrade, and application startup /
  health check.

The project adds `pytest-playwright` as a development dependency and uses Chromium
for interaction tests. CI installs the pinned Playwright Chromium build before
Pytest. This keeps the test API in Python while providing a real browser for
drag/drop and queue behavior that HTTP-only tests cannot verify.

## Remaining processor roadmap

The unified uploader is complete only as an entry point; follow-on work must add
processors for every account-statement category that V1 displays as unavailable.
Each processor requires its own approved design and implementation plan before its
catalog entry receives a processor key.

- `retirement_401k_statement` and `brokerage_statement` processors must extract a
  candidate account balance, as-of date, institution, and account identity; require
  editable confirmation; associate or create the correct asset account; and save a
  confirmed `AccountBalanceSnapshot`.
- `mortgage_statement` and `loan_statement` processors must extract candidate
  outstanding principal, as-of date, lender, and account identity; require editable
  confirmation; associate or create the correct liability account; and save a
  confirmed `AccountBalanceSnapshot`.
- `other_account_statement` must provide a conservative account-type and
  asset/liability selection with manual balance and date confirmation when a more
  specific processor does not apply.

All remaining processors must support private bounded uploads, validate actual
file contents, prefer embedded PDF text before local OCR, use synthetic fixtures,
and create no balance snapshot until the member confirms the extracted or entered
values. They must preserve workspace isolation, integer-cent money storage, source
retention choices, safe cleanup, and redacted logging.

Implementing a processor changes only its catalog capability and adds its review
workflow; it must not require a rewrite of the multi-file queue. Completion is
tracked in PR 8b of `docs/where-is-my-money-pr-breakdown.md`.

## Future AI/ML classification

A future classifier may suggest a category and confidence for each file while the
same native dropdown remains editable. It must never silently process a
low-confidence or unknown result. Manual selection remains the safe fallback.

Before model work begins, a separate design must decide:

- whether inference is local or uses an explicitly enabled external provider;
- what document text or metadata may be sent to the model;
- how consent, retention, logging, and provider failure are handled;
- confidence thresholds and evaluation metrics for each category;
- how synthetic evaluation documents cover layout and institution variation;
- how model suggestions are distinguished from user-confirmed categories.

Real user financial documents must not become source-controlled fixtures or model
training data. V1 creates no placeholder classifier service and makes no model
dependency mandatory.

## Future category administration

Category administration is a separate feature because the application currently
has workspace membership but no administrator role. Its design must define who
can manage global versus workspace-specific categories and must include an audit
trail for changes.

An eventual admin interface may manage category labels, aliases, visibility, and
ordering. It may not assign arbitrary executable code or claim processing support.
Processor keys remain an allow-listed capability shipped with application code.
Adding a display category therefore never makes document parsing available by
itself.
