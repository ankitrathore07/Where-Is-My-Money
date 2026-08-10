# PR 6 Payslip Income Imports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a private PDF/image payslip workflow that extracts locally, requires editable confirmation, creates confirmed income records only, and reports workspace-scoped gross/net totals.

**Architecture:** Add a focused `app/payslips/` package with separate storage, extraction, parsing, service, and route boundaries. Embedded PDF text uses `pypdf`; scanned PDFs render locally with `pypdfium2`; Pillow validates images; a fixed-argument Tesseract subprocess provides local OCR behind an injectable protocol. Existing `Payslip`, `IncomeRecord`, and `UploadedFile` tables hold review state, confirmed data, and private source metadata.

**Tech Stack:** Python 3.12, FastAPI/Jinja, SQLAlchemy, pypdf, pypdfium2, Pillow, local Tesseract, Pytest, Ruff, uv.

## Global Constraints

- Accept PDF, PNG, and JPEG payslips up to 10 MiB.
- Do not send source bytes, extracted text, or financial values to any network service or application log.
- Every read and write is authorized through workspace membership and scoped by `workspace_id`.
- Store money as integer cents and create no `IncomeRecord` before explicit confirmation.
- Confirmation must never create, update, or link a `Transaction`.
- Keep source files private and honor retain or delete-after-confirmation.
- Use only representative synthetic data in fixtures and tests.
- No schema migration is required; PR 2c already supplies the tables.

---

### Task 1: Private payslip storage and locked local dependencies

**Files:**
- Create: `app/payslips/__init__.py`
- Create: `app/payslips/storage.py`
- Create: `tests/payslips/__init__.py`
- Create: `tests/payslips/test_storage.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `StoredPayslipUpload(storage_key: str, checksum: str, size_bytes: int)`.
- Produces: `PayslipUploadStore(root: Path, max_bytes: int)` with `save(workspace_id: int, suffix: str, stream: BinaryIO)`, `read(storage_key: str)`, and `delete(storage_key: str)`.
- Produces: `PayslipStorageError(code: str, message: str)`.

- [ ] **Step 1: Add failing storage tests**

```python
def test_save_uses_opaque_workspace_key(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path, max_bytes=100)
    saved = store.save(7, ".pdf", BytesIO(b"%PDF-synthetic"))
    assert re.fullmatch(r"7/[0-9a-f]{32}\.pdf", saved.storage_key)
    assert store.read(saved.storage_key) == b"%PDF-synthetic"

def test_oversize_removes_partial_source(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path, max_bytes=4)
    with pytest.raises(PayslipStorageError, match="at most 4 bytes"):
        store.save(7, ".png", BytesIO(b"12345"))
    assert list(tmp_path.rglob("*.*")) == []
```

Also cover canonical `.jpeg` to `.jpg`, unsupported suffixes, invalid workspace IDs, traversal reads, missing reads, and idempotent delete.

- [ ] **Step 2: Run storage tests and verify RED**

Run: `uv run pytest tests/payslips/test_storage.py -v --basetemp data/pytest-task1`

Expected: collection fails because `app.payslips.storage` does not exist.

- [ ] **Step 3: Implement minimal bounded private storage**

Use 64 KiB streaming chunks, SHA-256, `uuid.uuid4().hex`, a strict key regex of `^[1-9]\d*/[0-9a-f]{32}\.(pdf|png|jpg)$`, `Path.resolve()` containment checks, exclusive file creation, and partial-file cleanup in `finally`.

- [ ] **Step 4: Add and lock local document dependencies**

Run: `uv add pypdf pypdfium2 pillow`

Verify that `pyproject.toml` lists the three direct dependencies and `uv.lock` changes without adding an OCR web client.

- [ ] **Step 5: Run storage tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_storage.py -v --basetemp data/pytest-task1`

Expected: all storage tests pass.

### Task 2: Deterministic candidate parsing and review validation

**Files:**
- Create: `app/payslips/parsing.py`
- Create: `tests/fixtures/payslips/synthetic_paystub_text.txt`
- Create: `tests/payslips/test_parsing.py`

**Interfaces:**
- Produces: `PayslipCandidate` with optional employer/date/pay-period fields, integer-cent gross/net/tax/deduction fields, and `to_json()`.
- Produces: `ReviewValues` with validated values ready for `Payslip`/`IncomeRecord` models.
- Produces: `ReviewValidationError(field_errors: dict[str, str])`.
- Produces: `extract_candidates(text: str) -> PayslipCandidate`.
- Produces: `validate_review(form: Mapping[str, str]) -> ReviewValues`.

- [ ] **Step 1: Add a synthetic payslip text fixture and failing candidate tests**

Fixture values:

```text
Employer: Northstar Bicycle Works
Pay Period: 2026-07-01 - 2026-07-15
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Taxes: $900.00
Deductions: $400.00
Net Pay: $3,700.00
```

Assert exact literal cents (`500000`, `370000`, `90000`, `40000`) and ISO date strings. Add cases for comma-free values, parenthesized amounts, missing labels, and labels that must not consume unrelated numbers.

- [ ] **Step 2: Run parser tests and verify RED**

Run: `uv run pytest tests/payslips/test_parsing.py -v --basetemp data/pytest-task2`

Expected: collection fails because `app.payslips.parsing` does not exist.

- [ ] **Step 3: Implement label-based extraction**

Normalize whitespace per line, use case-insensitive anchored label patterns, parse money with `Decimal`, and return `None` for a missing or malformed candidate instead of inventing a value. Candidate serialization uses ISO dates and integer cents.

- [ ] **Step 4: Add failing editable review validation tests**

```python
def test_review_uses_edited_literal_values() -> None:
    values = validate_review({
        "employer": "Edited Employer",
        "pay_period_start": "2026-07-02",
        "pay_period_end": "2026-07-16",
        "pay_date": "2026-07-21",
        "gross_pay": "5100.25",
        "net_pay": "3800.10",
        "taxes": "900.00",
        "deductions": "400.15",
    })
    assert values.gross_pay_cents == 510025
    assert values.net_pay_cents == 380010
```

Add field-specific failures for missing pay date, negative or over-precise money, non-numeric input, employer longer than 255 characters, and pay-period end before start.

- [ ] **Step 5: Run validation tests and verify RED**

Run: `uv run pytest tests/payslips/test_parsing.py -v --basetemp data/pytest-task2`

Expected: review tests fail because `validate_review` is not implemented.

- [ ] **Step 6: Implement minimal strict review normalization**

Accept optional blank employer/pay-period dates, require a valid ISO pay date, require non-negative amounts with at most two decimal places, and collect every field error before raising `ReviewValidationError`.

- [ ] **Step 7: Run parser tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_parsing.py -v --basetemp data/pytest-task2`

Expected: all parsing and validation tests pass.

### Task 3: Embedded text extraction and local OCR fallback

**Files:**
- Create: `app/payslips/extraction.py`
- Create: `tests/payslips/pdf_helpers.py`
- Create: `tests/payslips/test_extraction.py`

**Interfaces:**
- Produces: `OcrEngine` protocol with `extract_png(image_bytes: bytes) -> str`.
- Produces: `TesseractOcrEngine(executable: str = "tesseract")`.
- Produces: `ExtractedText(text: str, method: Literal["embedded_text", "ocr"])`.
- Produces: `DocumentExtractionError(code: str, message: str)`.
- Produces: `DocumentExtractor(ocr_engine: OcrEngine, max_pdf_pages: int = 10).extract(data: bytes, suffix: str) -> ExtractedText`.

- [ ] **Step 1: Add a test-only minimal text-PDF builder**

`tests/payslips/pdf_helpers.py` creates a valid one-page Helvetica PDF from synthetic ASCII lines and calculates xref byte offsets at runtime. This avoids a binary fixture and exercises the real `pypdf` boundary.

- [ ] **Step 2: Add failing embedded-text tests**

Assert that a generated PDF returns the literal synthetic fixture text with method `embedded_text` and never calls a fake OCR engine that raises if invoked. Add malformed PDF, encrypted PDF, empty document, and page-limit cases.

- [ ] **Step 3: Run embedded-text tests and verify RED**

Run: `uv run pytest tests/payslips/test_extraction.py -v --basetemp data/pytest-task3`

Expected: collection fails because `app.payslips.extraction` does not exist.

- [ ] **Step 4: Implement PDF signature validation and embedded extraction**

Require `%PDF-`, parse with `PdfReader(strict=False)`, reject encrypted documents and more than 10 pages, join extracted page text, and use embedded text only when at least 20 non-whitespace characters are present.

- [ ] **Step 5: Add failing scanned-PDF and image OCR tests**

Use `PdfWriter.add_blank_page()` for a scanned-PDF stand-in and Pillow to create a synthetic PNG in memory. A recording fake OCR engine returns the committed synthetic fixture text. Assert method `ocr`, non-empty PNG input, and correct fallback for both file types. Separately patch `subprocess.run` to verify fixed Tesseract arguments, `shell=False`, standard-input image bytes, successful stdout decoding, missing executable guidance, timeout, and nonzero-exit handling.

- [ ] **Step 6: Run OCR tests and verify RED**

Run: `uv run pytest tests/payslips/test_extraction.py -v --basetemp data/pytest-task3`

Expected: OCR fallback tests fail because rendering and `TesseractOcrEngine` are incomplete.

- [ ] **Step 7: Implement local rendering, image normalization, and Tesseract boundary**

Render PDF pages at 200 DPI with `pypdfium2`, convert each page or uploaded image to RGB PNG with Pillow, and OCR sequentially. Invoke `subprocess.run([executable, "stdin", "stdout", "-l", "eng", "--psm", "6"], input=image_bytes, capture_output=True, timeout=30, check=False, shell=False)`. Convert native failures into safe `DocumentExtractionError` messages and never include OCR output or source bytes in logs/errors.

- [ ] **Step 8: Run extraction tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_extraction.py -v --basetemp data/pytest-task3`

Expected: all extraction tests pass.

### Task 4: Pending payslips, explicit confirmation, retention, and summaries

**Files:**
- Create: `app/payslips/service.py`
- Create: `tests/payslips/test_service.py`

**Interfaces:**
- Consumes: `PayslipUploadStore`, `DocumentExtractor`, `extract_candidates`, and `validate_review`.
- Produces: `create_payslip_import(session, store, extractor, workspace, stream, suffix, retention_choice) -> Payslip`.
- Produces: `get_workspace_payslip(session, workspace_id, payslip_id) -> Payslip | None`.
- Produces: `ConfirmationResult(record: IncomeRecord, cleanup_failed: bool, already_confirmed: bool)`.
- Produces: `confirm_payslip(session, store, payslip, form) -> ConfirmationResult`.
- Produces: `IncomeSummary(records: tuple[IncomeRecord, ...], record_count: int, gross_pay_cents: int, net_pay_cents: int)`.
- Produces: `get_income_summary(session, workspace_id) -> IncomeSummary`.
- Produces: `PayslipImportError(code: str, message: str)`.

- [ ] **Step 1: Add failing pending-import tests**

Assert one valid upload creates an `UploadedFile` and pending `Payslip` with candidate JSON and confidence, but `select(func.count(IncomeRecord.id))` remains zero. Assert invalid retention and extraction failure create no database rows and remove the private source.

- [ ] **Step 2: Run pending-import tests and verify RED**

Run: `uv run pytest tests/payslips/test_service.py -v --basetemp data/pytest-task4`

Expected: collection fails because `app.payslips.service` does not exist.

- [ ] **Step 3: Implement atomic pending import creation**

Validate retention before saving, save/extract/parse, add `UploadedFile(file_type, checksum, size_bytes, retention_choice)` and `Payslip(review_status="pending", candidate_fields=...)`, commit together, and delete the source plus rollback on any failure.

- [ ] **Step 4: Add failing confirmation tests**

Assert edited values, payslip dates/status, and one income record after confirmation; zero transactions before and after; no writes on validation errors; idempotent second confirmation; delete-after-confirmation cleanup; retained source behavior; cleanup failure status; and workspace-scoped lookup.

- [ ] **Step 5: Run confirmation tests and verify RED**

Run: `uv run pytest tests/payslips/test_service.py -v --basetemp data/pytest-task4`

Expected: tests fail because confirmation behavior is missing.

- [ ] **Step 6: Implement confirmation and post-commit cleanup**

Normalize the submitted form, return an existing income row for an already-confirmed payslip, update the payslip and insert one `IncomeRecord` in one database commit, then perform optional source deletion. A deletion exception changes status to `confirmed_cleanup_failed` without deleting income; successful cleanup marks `UploadedFile.deleted` and leaves status `confirmed`.

- [ ] **Step 7: Add failing summary tests**

Create literal confirmed records in two workspaces and assert the target summary includes only its rows and exact hand-calculated gross/net totals. Assert a workspace with no confirmed income reports zeros and pending candidate amounts are excluded.

- [ ] **Step 8: Run summary tests and verify RED**

Run: `uv run pytest tests/payslips/test_service.py -v --basetemp data/pytest-task4`

Expected: summary tests fail because aggregation is missing.

- [ ] **Step 9: Implement workspace-only aggregate summary**

Use SQL `count`, `coalesce(sum(gross_pay_cents), 0)`, and `coalesce(sum(net_pay_cents), 0)` filtered by `IncomeRecord.workspace_id`, plus a newest-first record query with the same filter.

- [ ] **Step 10: Run service tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_service.py -v --basetemp data/pytest-task4`

Expected: all service tests pass.

### Task 5: Authorized upload, review, confirmation, and income pages

**Files:**
- Create: `app/payslips/routes.py`
- Create: `app/templates/payslips/upload.html`
- Create: `app/templates/payslips/review.html`
- Create: `app/templates/payslips/income.html`
- Create: `tests/payslips/test_routes.py`
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Modify: `app/templates/workspace_detail.html`

**Interfaces:**
- Consumes: existing `require_current_user`, `require_workspace`, `require_csrf`, `get_db`, and Task 4 services.
- Produces routes: `GET /workspaces/{workspace_id}/payslips/new`, `POST /workspaces/{workspace_id}/payslips`, `GET /workspaces/{workspace_id}/payslips/{payslip_id}/review`, `POST /workspaces/{workspace_id}/payslips/{payslip_id}/confirm`, and `GET /workspaces/{workspace_id}/income`.
- Produces setting: `max_payslip_upload_bytes: int = 10 * 1024 * 1024`.

- [ ] **Step 1: Add failing route authorization and validation tests**

Using `build_route_test_app`, assert authentication redirects, CSRF rejects before file/database mutation, nonmembers receive 404, unsupported extensions/content types create no files/rows, and foreign payslip IDs return 404 from review and confirm routes.

- [ ] **Step 2: Run route tests and verify RED**

Run: `uv run pytest tests/payslips/test_routes.py -v --basetemp data/pytest-task5`

Expected: route requests return 404 because the router is not registered.

- [ ] **Step 3: Register feature objects and implement upload routes/templates**

Add the payslip size setting; initialize `PayslipUploadStore` and `DocumentExtractor(TesseractOcrEngine())` on application state; register the router; validate `.pdf/.png/.jpg/.jpeg` plus matching PDF/PNG/JPEG media types; and render beginner-readable local-processing/retention guidance.

- [ ] **Step 4: Add failing review/confirmation tests**

Override the app extractor with a deterministic fake returning the synthetic text. Assert valid upload redirects to a review page containing editable candidate inputs, no income exists before confirmation, edited confirmation values persist once, and invalid confirmation redisplays submitted values and field errors.

- [ ] **Step 5: Run review/confirmation route tests and verify RED**

Run: `uv run pytest tests/payslips/test_routes.py -v --basetemp data/pytest-task5`

Expected: tests fail because review and confirmation routes/templates are missing.

- [ ] **Step 6: Implement review and confirmation routes/templates**

Load payslips with `workspace_id`, convert candidate cents to editable decimal strings, preserve submitted form data on validation errors, apply CSRF to confirmation, redirect success to `/income`, and show a cleanup warning through a query parameter when necessary.

- [ ] **Step 7: Add failing income-page tests**

Assert exact `$5,000.00` gross and `$3,700.00` net totals, newest-first confirmed records, empty-state zeros, no pending candidate values, and no values from another workspace.

- [ ] **Step 8: Run income page tests and verify RED**

Run: `uv run pytest tests/payslips/test_routes.py -v --basetemp data/pytest-task5`

Expected: income route/template is incomplete.

- [ ] **Step 9: Implement income page and navigation**

Render summary cards and confirmed-record table with a local cents-format helper. Add Payslips/Income navigation links to the workspace detail page and workspace-aware header.

- [ ] **Step 10: Run route tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_routes.py -v --basetemp data/pytest-task5`

Expected: all route tests pass.

### Task 6: End-to-end synthetic acceptance, documentation, and verification

**Files:**
- Create: `tests/payslips/test_acceptance.py`
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Modify: `Dockerfile`

**Interfaces:**
- Consumes: complete routes, real embedded-PDF extraction, fake native OCR boundary, and income summary.
- Produces: documented local Tesseract requirement and a Docker image containing Tesseract English data.

- [ ] **Step 1: Add failing text-PDF acceptance flow**

Generate a synthetic text PDF using the test helper, sign in, upload it, assert no income before review, confirm it, and assert exact gross/net totals plus zero new transactions.

- [ ] **Step 2: Run the integrated text-PDF acceptance flow**

Run: `uv run pytest tests/payslips/test_acceptance.py::test_text_pdf_requires_confirmation_and_updates_income_totals -v --basetemp data/pytest-task6`

Expected: it passes without mocking embedded PDF extraction. This is an integration
check of behaviors already introduced test-first in Tasks 1-5, not a new production
behavior.

- [ ] **Step 3: Add scanned-image and scanned-PDF acceptance flows**

Create a synthetic PNG and a blank scanned-PDF stand-in in memory, replace only `OcrEngine` with a fake returning the committed synthetic text, and assert each workflow requires confirmation and produces the exact isolated totals without transactions.

- [ ] **Step 4: Run acceptance tests and verify GREEN**

Run: `uv run pytest tests/payslips/test_acceptance.py -v --basetemp data/pytest-task6`

Expected: text and scanned workflows pass.

- [ ] **Step 5: Document the beginner workflow and local software boundary**

Update README setup, payslip instructions, project map, checks, and privacy explanation. Explain that text PDFs do not require OCR; images/scanned PDFs require the local `tesseract` executable; nothing is uploaded to an OCR service. Mark PR 6 implemented in the breakdown. Install `tesseract-ocr` and `tesseract-ocr-eng` in the Debian Docker image so container users receive the local executable.

- [ ] **Step 6: Run focused payslip tests**

Run: `uv run pytest tests/payslips tests/test_payslips.py -v --basetemp data/pytest-payslips`

Expected: all payslip tests pass with no warnings.

- [ ] **Step 7: Run fresh complete verification**

Run:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest --basetemp data/pytest-final
uv run alembic upgrade head
uv run python -c "from fastapi.testclient import TestClient; from app.main import create_app; c = TestClient(create_app()); assert c.get('/health').json() == {'status': 'ok'}"
```

Use a fresh temporary SQLite `DATABASE_URL` for the migration/startup commands and verify every command exits zero.

- [ ] **Step 8: Review the final diff against the design**

Check authorization, confirmation-only persistence, no transaction writes, local-only extraction, cleanup truthfulness, synthetic-only fixtures, dependency lock changes, and README accuracy. Run `git diff --check` and inspect `git status --short`.

- [ ] **Step 9: Commit, push, and open the ready PR**

Create branch `codex/pr-6-payslip-income-imports`, commit the verified implementation, push it to `origin`, and use `gh pr create --base main` with a ready (not draft) PR body containing summary, security/privacy notes, local Tesseract requirement, migration/startup evidence, Ruff evidence, and full Pytest evidence. Do not merge.
