# PR 6 Payslip Income Imports Design

## Goal

Add a private, workspace-scoped payslip workflow that accepts PDF and image
files, extracts candidate pay details locally, requires an editable confirmation,
and reports gross and net totals from confirmed income records without creating a
bank transaction.

## Scope and constraints

- Accept PDF, PNG, and JPEG payslips up to 10 MiB.
- Keep document processing local. The application has no network extraction path
  and never sends a financial document to an external service.
- Try embedded PDF text before OCR. Render scanned PDF pages locally and use the
  same local OCR boundary used for image uploads.
- Require an authenticated workspace membership on every payslip route. A missing
  or foreign payslip returns the same 404 response.
- Store only integer cents for money and validate dates and amounts again when the
  review form is submitted.
- A pending `Payslip` may hold extracted candidate fields for review, but an
  `IncomeRecord` is created only by the explicit confirmation form.
- Never create, update, or link a `Transaction` while confirming a payslip.
- Let the member retain the private source or delete it after successful
  confirmation, matching the existing upload-retention policy.
- Use synthetic documents and values in tests. Do not log source text or extracted
  financial values.

## Architecture

Create an `app/payslips/` feature package that follows the existing modular
FastAPI structure:

- `storage.py` stores only validated extension types beneath opaque,
  workspace-prefixed keys. It streams, hashes, enforces the size limit, prevents
  path traversal, and removes partial writes on failure.
- `extraction.py` owns document validation and text extraction. `pypdf` reads
  embedded PDF text; Pillow validates and normalizes images; `pypdfium2` renders
  scanned PDF pages; and a small `OcrEngine` interface isolates the local
  Tesseract subprocess. OCR receives PNG bytes through standard input, uses fixed
  arguments without a shell, and returns text through standard output.
- `parsing.py` contains deterministic, label-based candidate extraction and strict
  review normalization. It recognizes employer, pay period, pay date, gross pay,
  net pay, taxes, and deductions without guessing from unrelated numbers.
- `service.py` coordinates upload creation, workspace-scoped lookup, confirmation,
  retention cleanup, and aggregate income summaries.
- `routes.py` renders upload, review, and summary pages behind existing session,
  CSRF, and workspace dependencies.

The application factory creates default local storage and extraction objects on
`application.state`. Tests replace only the native OCR boundary or feature
objects that would otherwise invoke local system software.

## Data flow

### Upload and extraction

1. The member opens the payslip upload form within an authorized workspace.
2. Route-specific ASGI middleware bounds the complete multipart request before
   form parsing, including requests without a `Content-Length` header.
3. The POST route validates the filename extension and required declared content
   type.
4. The storage service streams the source to an opaque private key while enforcing
   the 10 MiB maximum and calculating SHA-256.
5. The extraction service validates the actual file signature and parses the
   source. A PDF with meaningful embedded text uses that text. A text-empty PDF is
   rendered and OCRed locally one page at a time, releasing each page before the
   next is rendered. PNG/JPEG sources are normalized to PNG and OCRed locally.
6. Deterministic parsing creates candidate fields and a coarse confidence value
   based on the extraction method and field coverage.
7. One `UploadedFile` and one pending `Payslip` are committed together. Extraction
   failure removes the private source and creates no database records.
8. The browser is redirected to the editable review page.

PDF processing is limited to 10 pages. Uploaded images and rendered PDF pages
share an explicit 40-million-pixel safety limit that is checked before image
decoding or page rendering. Encrypted, malformed, empty, oversized, or unsupported
documents return a safe validation message.

### Review and confirmation

The review form displays employer, pay-period start/end, pay date, gross pay, net
pay, taxes, and deductions. Missing or low-confidence values remain visibly
editable; the user cannot bypass the form.

On confirmation, the service:

1. Reloads the payslip through both `payslip_id` and `workspace_id`.
2. Normalizes the submitted values independently of extraction output.
3. Requires a pay date and non-negative gross, net, tax, and deduction amounts;
   limits text and money values to database-safe bounds; and rejects a pay-period
   end before its start. Extracted suggestions use the same bounds before they
   enter candidate JSON.
4. Creates exactly one `IncomeRecord`, updates the payslip's reviewed fields and
   status, and commits the database transaction. A unique database index on
   `payslip_id` makes this invariant atomic even when two confirmations arrive
   together; the losing request reloads and returns the existing record.
5. If delete-after-confirmation was selected, removes the source after the
   database commit and records cleanup failure without rolling back confirmed
   income.

Repeated confirmation is idempotent: it returns the existing confirmed record
instead of inserting a duplicate. Confirmation code does not import or construct
the `Transaction` model.

## Income summary

`GET /workspaces/{workspace_id}/income` lists confirmed income records newest
first and calculates record count, gross cents, and net cents with SQL aggregates
scoped to that workspace. Empty workspaces show zero totals. Pending payslips are
not included.

## Error handling

- Extension/content-type mismatch, invalid signatures, malformed documents,
  oversize sources, too many PDF pages, empty extraction, and unavailable/failed
  OCR produce beginner-readable 400 responses.
- Missing Tesseract guidance names the executable and explains that it runs
  locally; it does not suggest uploading the document elsewhere.
- Review validation redisplays submitted values and field-specific errors without
  creating income.
- Foreign workspace and payslip identifiers return 404.
- Failed source deletion leaves the confirmed income intact and shows a truthful
  cleanup warning.

## Testing strategy

Use strict red-green-refactor cycles for each behavior:

- Storage tests: opaque workspace keys, size cleanup, allowed extensions, read and
  delete behavior, and traversal rejection.
- Parsing tests: representative synthetic OCR text, currency formats, optional
  fields, invalid dates, invalid money, and pay-period ordering.
- Extraction tests: embedded PDF text, scanned-PDF and image OCR fallback through
  a fake OCR engine, incremental multi-page processing, pixel/page limits,
  malformed files, and unavailable Tesseract.
- Service tests: pending candidates create no income, confirmed edited values,
  sequential and simultaneous idempotent confirmation, retention cleanup,
  cleanup failure, workspace scoping, and no transaction side effect.
- Summary tests: exact hand-calculated gross/net totals and isolation between two
  workspaces.
- Route/acceptance tests: authentication and CSRF, upload validation, foreign
  workspace/payslip 404 behavior, editable review, text-PDF confirmation, scanned
  image confirmation with the native OCR boundary mocked, and correct final
  totals.

The final gate runs Ruff lint, Ruff format check, the complete Pytest suite, an
Alembic upgrade of a fresh SQLite database, and an application startup/health
check. Migration `0008` replaces the old non-unique payslip lookup index with a
unique index so one payslip cannot produce duplicate confirmed income.

## Dependency and setup impact

Python dependencies add `pypdf`, `pypdfium2`, and Pillow for local PDF/image
handling. OCR additionally requires the Tesseract executable with English
language data on the machine or in the Docker image. Text-based PDFs work without
Tesseract; image and scanned-PDF imports show a clear local setup error when it is
missing.
