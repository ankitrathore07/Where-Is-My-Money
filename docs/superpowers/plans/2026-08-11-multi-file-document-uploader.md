# Multi-File Document Uploader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one workspace-scoped page where a member can browse or drag and drop up to 10 documents, manually choose each document category, remove unwanted rows, and sequentially send supported transaction statements and payslips into their existing review workflows.

**Architecture:** A new `app/documents/` package owns the immutable category catalog, unified page, and one-file JSON dispatch endpoint. Vanilla JavaScript keeps files in a browser-only queue and sends ready rows sequentially; the server revalidates every request and delegates to the existing CSV or payslip service. Processor-less categories remain visible but never leave the browser, and no batch or category database model is added.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, vanilla JavaScript, SQLAlchemy, Pytest, pytest-playwright with Chromium, Ruff, uv.

## Global Constraints

- V1 does not classify, infer, preselect, or silently change a document category; every queued row begins with `Choose a category`.
- Accept only CSV, PDF, PNG, and JPEG files and keep at most 10 files in the browser queue.
- Transaction statements must be CSV and keep the existing 5 MiB server limit.
- Payslips must be PDF, PNG, or JPEG and keep the existing 10 MiB, 10-page, and 40-million-pixel safety limits.
- Upload ready rows sequentially, one multipart request per file; one failure must not roll back another file.
- Unsupported, unlisted, mismatched, and oversized files must not enter private application storage.
- Keep the existing CSV and payslip upload pages as compatibility and non-JavaScript fallbacks.
- Default to deleting each raw source after its existing successful commit/confirmation point; allow one batch-wide `retain` choice.
- Keep processing local to the application machine. Add no external document service, AI/ML model, batch table, category table, or Alembic migration.
- Preserve authentication, workspace isolation, CSRF, opaque private storage, safe cleanup, integer-cent money, and redacted logging.
- Use synthetic documents only in tests; never log or commit real financial documents or extracted values.
- Remaining 401(k), brokerage/stocks, mortgage, loan, and other-account processors stay follow-on PR 8b work and receive no processor key in this plan.

## File Structure

### New files

- `app/documents/__init__.py` — package marker.
- `app/documents/types.py` — immutable category and successful processing result contracts.
- `app/documents/catalog.py` — stable manual categories, metadata validation, and client-safe catalog serialization.
- `app/documents/routes.py` — authorized unified page plus one-file JSON dispatch endpoint.
- `app/templates/documents/upload.html` — semantic drop zone, queue table shell, retention controls, live regions, and fallback links.
- `app/static/document-upload.js` — browser queue state, category validation, drag/drop, removal, sequential requests, retry, and result links.
- `tests/documents/__init__.py` — test package marker.
- `tests/documents/test_catalog.py` — category and metadata validation tests.
- `tests/documents/test_routes.py` — HTTP dispatch, authorization, CSRF, delegation, and no-mutation tests.
- `tests/documents/test_page.py` — server-rendered uploader and workspace-entry tests.
- `tests/documents/conftest.py` — live test server, fake local extraction, and authenticated Playwright fixtures.
- `tests/documents/test_upload_queue_browser.py` — real-browser queue and batch-flow tests.

### Modified files

- `app/core/middleware.py` — bound both the legacy payslip endpoint and the new document endpoint before multipart parsing.
- `app/main.py` — register the document router and generalized body-limit middleware.
- `app/templates/workspace_detail.html` — make `Upload documents` the primary import entry point while retaining transaction and income destinations.
- `app/static/styles.css` — compact responsive queue, drop-target, status, and icon-button styling.
- `tests/test_security.py` — preserve payslip body-limit behavior and cover the new route.
- `tests/test_auth_routes.py` — update workspace-detail copy assertion.
- `tests/route_helpers.py` — make the shared route app's payslip store use the per-test upload root.
- `pyproject.toml` and `uv.lock` — add the Python Playwright test plugin.
- `.github/workflows/ci.yml` — install the pinned Chromium build before Pytest.
- `README.md` — document unified manual categorization, supported processors, fallbacks, and remaining processor roadmap.

---

### Task 1: Define the manual document catalog

**Files:**
- Create: `app/documents/__init__.py`
- Create: `app/documents/types.py`
- Create: `app/documents/catalog.py`
- Create: `tests/documents/__init__.py`
- Create: `tests/documents/test_catalog.py`

**Interfaces:**
- Consumes: no new feature interfaces.
- Produces: `DocumentCategory`, `DocumentProcessResult`, `DocumentUploadValidationError`, `DOCUMENT_CATEGORIES`, `ALLOWED_QUEUE_SUFFIXES`, `MAX_QUEUE_FILES`, `get_document_category()`, `validate_processable_upload()`, and `client_catalog()`.

- [ ] **Step 1: Write failing catalog tests**

Create `tests/documents/test_catalog.py` with the exact stable keys and validation behavior:

```python
import pytest

from app.documents.catalog import (
    ALLOWED_QUEUE_SUFFIXES,
    DOCUMENT_CATEGORIES,
    MAX_QUEUE_FILES,
    DocumentUploadValidationError,
    client_catalog,
    validate_processable_upload,
)


EXPECTED_KEYS = (
    "transaction_statement",
    "payslip",
    "retirement_401k_statement",
    "brokerage_statement",
    "mortgage_statement",
    "loan_statement",
    "other_account_statement",
    "unlisted",
)


def test_catalog_exposes_stable_manual_categories_and_only_two_processors() -> None:
    assert tuple(category.key for category in DOCUMENT_CATEGORIES) == EXPECTED_KEYS
    assert {category.key for category in DOCUMENT_CATEGORIES if category.processor} == {
        "transaction_statement",
        "payslip",
    }
    assert ALLOWED_QUEUE_SUFFIXES == frozenset({".csv", ".pdf", ".png", ".jpg", ".jpeg"})
    assert MAX_QUEUE_FILES == 10


@pytest.mark.parametrize(
    "key,filename,content_type,processor",
    [
        ("transaction_statement", "checking.csv", "text/csv", "csv_import"),
        ("transaction_statement", "checking.csv", "application/octet-stream", "csv_import"),
        ("payslip", "pay.pdf", "application/pdf", "payslip"),
        ("payslip", "pay.jpeg", "image/jpeg", "payslip"),
    ],
)
def test_processable_metadata_returns_the_selected_category(
    key: str, filename: str, content_type: str, processor: str
) -> None:
    category = validate_processable_upload(key, filename, content_type)
    assert category.key == key
    assert category.processor == processor


@pytest.mark.parametrize(
    "key,filename,content_type,code",
    [
        ("missing", "checking.csv", "text/csv", "unknown_category"),
        ("brokerage_statement", "account.pdf", "application/pdf", "processor_unavailable"),
        ("unlisted", "account.pdf", "application/pdf", "processor_unavailable"),
        ("transaction_statement", "checking.pdf", "application/pdf", "category_format_mismatch"),
        ("payslip", "pay.pdf", "text/plain", "category_format_mismatch"),
    ],
)
def test_invalid_metadata_has_a_safe_stable_error(
    key: str, filename: str, content_type: str, code: str
) -> None:
    with pytest.raises(DocumentUploadValidationError) as error:
        validate_processable_upload(key, filename, content_type)
    assert error.value.code == code
    assert error.value.message


def test_client_catalog_contains_no_classifier_or_server_only_objects() -> None:
    payload = client_catalog(max_csv_bytes=5_000_000, max_payslip_bytes=10_000_000)
    assert payload[0] == {
        "key": "transaction_statement",
        "label": "Bank or credit-card transaction statement",
        "supported": True,
        "accepted_suffixes": [".csv"],
        "max_bytes": 5_000_000,
    }
    assert payload[2]["supported"] is False
    assert payload[2]["accepted_suffixes"] == []
    assert "suggestion" not in payload[0]
    assert "confidence" not in payload[0]
```

- [ ] **Step 2: Run the catalog tests to verify they fail**

Run:

```powershell
uv run pytest tests/documents/test_catalog.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.documents'`.

- [ ] **Step 3: Add immutable feature contracts**

Create an empty `app/documents/__init__.py`, then create `app/documents/types.py`:

```python
from dataclasses import dataclass
from typing import Literal, Mapping

DocumentProcessorKey = Literal["csv_import", "payslip"]


@dataclass(frozen=True)
class DocumentCategory:
    key: str
    label: str
    processor: DocumentProcessorKey | None
    content_types_by_suffix: Mapping[str, frozenset[str]]

    @property
    def accepted_suffixes(self) -> frozenset[str]:
        return frozenset(self.content_types_by_suffix)


@dataclass(frozen=True)
class DocumentProcessResult:
    message: str
    next_url: str
    next_label: str

    def as_payload(self) -> dict[str, bool | str]:
        return {
            "ok": True,
            "message": self.message,
            "next_url": self.next_url,
            "next_label": self.next_label,
        }
```

- [ ] **Step 4: Implement the code-defined category catalog and validator**

Create `app/documents/catalog.py`. Use these public constants and functions; keep the error copy exact so the route and browser can agree:

```python
from pathlib import Path

from app.documents.types import DocumentCategory

MAX_QUEUE_FILES = 10
ALLOWED_QUEUE_SUFFIXES = frozenset({".csv", ".pdf", ".png", ".jpg", ".jpeg"})
CSV_CONTENT_TYPES = frozenset(
    {"text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"}
)
PAYSLIP_CONTENT_TYPES = {
    ".pdf": frozenset({"application/pdf"}),
    ".png": frozenset({"image/png"}),
    ".jpg": frozenset({"image/jpeg", "image/jpg"}),
    ".jpeg": frozenset({"image/jpeg", "image/jpg"}),
}


class DocumentUploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


DOCUMENT_CATEGORIES = (
    DocumentCategory(
        "transaction_statement",
        "Bank or credit-card transaction statement",
        "csv_import",
        {".csv": CSV_CONTENT_TYPES},
    ),
    DocumentCategory("payslip", "Payslip", "payslip", PAYSLIP_CONTENT_TYPES),
    DocumentCategory("retirement_401k_statement", "401(k) retirement statement", None, {}),
    DocumentCategory("brokerage_statement", "Brokerage or stocks statement", None, {}),
    DocumentCategory("mortgage_statement", "Mortgage statement", None, {}),
    DocumentCategory("loan_statement", "Loan statement", None, {}),
    DocumentCategory("other_account_statement", "Other account statement", None, {}),
    DocumentCategory("unlisted", "Category not listed", None, {}),
)
_CATEGORY_BY_KEY = {category.key: category for category in DOCUMENT_CATEGORIES}


def get_document_category(key: str) -> DocumentCategory | None:
    return _CATEGORY_BY_KEY.get(key)


def validate_processable_upload(
    category_key: str, filename: str, content_type: str | None
) -> DocumentCategory:
    category = get_document_category(category_key)
    if category is None:
        raise DocumentUploadValidationError("unknown_category", "Choose a valid document category.")
    if category.processor is None:
        raise DocumentUploadValidationError(
            "processor_unavailable",
            "This document category is recognized, but processing is not available yet.",
        )
    suffix = Path(filename).suffix.casefold()
    allowed_types = category.content_types_by_suffix.get(suffix)
    normalized_type = (content_type or "").casefold()
    if allowed_types is None or normalized_type not in allowed_types:
        expected = "CSV" if category.processor == "csv_import" else "PDF, PNG, or JPEG"
        raise DocumentUploadValidationError(
            "category_format_mismatch", f"{category.label} files must use {expected}."
        )
    return category


def client_catalog(*, max_csv_bytes: int, max_payslip_bytes: int) -> list[dict[str, object]]:
    limits = {"csv_import": max_csv_bytes, "payslip": max_payslip_bytes}
    return [
        {
            "key": category.key,
            "label": category.label,
            "supported": category.processor is not None,
            "accepted_suffixes": sorted(category.accepted_suffixes),
            "max_bytes": limits.get(category.processor),
        }
        for category in DOCUMENT_CATEGORIES
    ]
```

- [ ] **Step 5: Run the focused tests and lint**

Run:

```powershell
uv run pytest tests/documents/test_catalog.py -v
uv run ruff check app/documents tests/documents/test_catalog.py
uv run ruff format --check app/documents tests/documents/test_catalog.py
```

Expected: all catalog tests pass and Ruff reports no issues.

- [ ] **Step 6: Commit the catalog boundary**

```powershell
git add app/documents tests/documents
git commit -m "feat: add document category catalog"
```

---

### Task 2: Bound the unified upload route before multipart parsing

**Files:**
- Modify: `app/core/middleware.py:13-86`
- Modify: `app/main.py:19,97-101`
- Modify: `tests/test_security.py:63-113`

**Interfaces:**
- Consumes: `Settings.max_payslip_upload_bytes` as the largest supported per-request file limit.
- Produces: `UploadBodyLimitMiddleware(app, max_file_bytes, multipart_overhead_bytes=64 * 1024)` targeting both `/payslips` and `/document-uploads` POST paths.

- [ ] **Step 1: Add failing middleware tests for both bounded routes**

Rename the existing test to `test_upload_body_limit_counts_streamed_chunks_without_content_length` and parameterize the route and message:

```python
@pytest.mark.parametrize(
    "path,message",
    [
        ("/workspaces/1/payslips", b"Payslip upload is too large."),
        ("/workspaces/1/document-uploads", b"Document upload is too large."),
    ],
)
def test_upload_body_limit_counts_streamed_chunks_without_content_length(
    path: str, message: bytes
) -> None:
    completed_downstream = False

    async def consuming_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal completed_downstream
        while True:
            incoming = await receive()
            if not incoming.get("more_body", False):
                break
        completed_downstream = True

    middleware = UploadBodyLimitMiddleware(
        consuming_app, max_file_bytes=5, multipart_overhead_bytes=0
    )
    incoming: list[Message] = [
        {"type": "http.request", "body": b"123", "more_body": True},
        {"type": "http.request", "body": b"456", "more_body": False},
    ]
    sent: list[Message] = []

    async def receive() -> Message:
        return incoming.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope = upload_scope(path)
    asyncio.run(middleware(scope, receive, send))

    assert completed_downstream is False
    assert sent[0]["status"] == 413
    assert sent[1]["body"] == message
```

Add this test helper and update the middleware import to `UploadBodyLimitMiddleware`:

```python
def upload_scope(path: str) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
```

- [ ] **Step 2: Run the middleware test to verify it fails**

Run:

```powershell
uv run pytest tests/test_security.py -k upload_body_limit -v
```

Expected: collection fails because `UploadBodyLimitMiddleware` does not exist.

- [ ] **Step 3: Generalize the middleware without changing legacy payslip copy**

In `app/core/middleware.py`, replace the payslip-only names with:

```python
BOUNDED_UPLOAD_PATH = re.compile(r"/workspaces/\d+/(?:payslips|document-uploads)")


class _UploadBodyTooLarge(Exception):
    """Stop multipart parsing once a bounded upload request is too large."""


class UploadBodyLimitMiddleware:
    """Bound supported upload bodies before Starlette spools uploaded files."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_file_bytes: int,
        multipart_overhead_bytes: int = 64 * 1024,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_file_bytes + multipart_overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._targets_upload(scope):
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is None or content_length <= self.max_body_bytes:
            received_bytes = 0

            async def limited_receive() -> Message:
                nonlocal received_bytes
                message = await receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > self.max_body_bytes:
                        raise _UploadBodyTooLarge
                return message

            try:
                await self.app(scope, limited_receive, send)
            except _UploadBodyTooLarge:
                await self._reject(scope, receive, send)
            return

        await self._reject(scope, receive, send)

    @staticmethod
    def _targets_upload(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and BOUNDED_UPLOAD_PATH.fullmatch(scope.get("path", "")) is not None
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        payslip_route = scope.get("path", "").endswith("/payslips")
        message = "Payslip upload is too large." if payslip_route else "Document upload is too large."
        response = PlainTextResponse(message, status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        await response(scope, receive, send)
```

Rename all internal `_PayslipBodyTooLarge`, `_targets_payslip_upload`, and catch references consistently.

- [ ] **Step 4: Register the generalized middleware**

Update `app/main.py`:

```python
from app.core.middleware import CSRFMiddleware, UploadBodyLimitMiddleware

# ...
application.add_middleware(
    UploadBodyLimitMiddleware,
    max_file_bytes=configured.max_payslip_upload_bytes,
)
```

The 10 MiB outer limit protects the new route; `LocalUploadStore` still enforces the stricter 5 MiB CSV limit after category validation.

- [ ] **Step 5: Run security and payslip route regressions**

Run:

```powershell
uv run pytest tests/test_security.py tests/payslips/test_routes.py -v
uv run ruff check app/core/middleware.py app/main.py tests/test_security.py
```

Expected: the new route test and the existing exact payslip 413 test pass.

- [ ] **Step 6: Commit the bounded route**

```powershell
git add app/core/middleware.py app/main.py tests/test_security.py
git commit -m "refactor: bound unified document uploads"
```

---

### Task 3: Add the one-file document dispatch API

**Files:**
- Create: `app/documents/routes.py`
- Modify: `app/main.py:13-28,103-108`
- Modify: `tests/route_helpers.py:49-70`
- Create: `tests/documents/test_routes.py`

**Interfaces:**
- Consumes: `validate_processable_upload()`, `DocumentProcessResult`, `create_csv_import()`, `create_payslip_import()`, existing application-state stores/extractor, `require_csrf`, and workspace/auth dependencies.
- Produces: `POST /workspaces/{workspace_id}/document-uploads` with the exact success/error JSON contract from the design.

- [ ] **Step 1: Make the shared test application isolate both upload stores**

Update `tests/route_helpers.py` so the route app does not leave payslip tests using a separately constructed default store:

```python
from app.payslips.storage import PayslipUploadStore

# inside build_route_test_app(...)
application.state.upload_store = LocalUploadStore(tmp_path)
application.state.payslip_store = PayslipUploadStore(tmp_path)
```

- [ ] **Step 2: Write failing security and unsupported-category route tests**

Create `tests/documents/test_routes.py` with the existing `build_route_test_app`, `complete_sign_in`, and `csrf_token` helpers. Add these concrete cases:

```python
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models import ImportJob, Payslip, UploadedFile, User, Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_document_upload_requires_csrf_before_storage(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={"category_key": "transaction_statement", "retention_choice": "retain"},
                files={"document": ("checking.csv", CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()
    assert response.status_code == 403
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
@pytest.mark.parametrize("category_key", ["brokerage_statement", "unlisted", "missing"])
async def test_unprocessable_category_never_reaches_private_storage(
    tmp_path: Path, category_key: str
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": category_key,
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("account.pdf", b"private", "application/pdf")},
            )
        with factory() as session:
            assert session.scalar(select(func.count(UploadedFile.id))) == 0
    finally:
        engine.dispose()
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert list(tmp_path.rglob("*.*")) == []
```

Add these focused cases for FastAPI field validation, workspace isolation, and metadata mismatch:

```python
@pytest.mark.anyio
async def test_document_upload_requires_exactly_one_file(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
            )
    finally:
        engine.dispose()
    assert response.status_code == 422


@pytest.mark.anyio
async def test_document_upload_hides_a_foreign_workspace(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                foreign_owner = User(
                    google_sub="foreign-document-owner",
                    email="foreign-document-owner@example.test",
                )
                foreign = Workspace(name="Foreign", is_personal=True, owner=foreign_owner)
                session.add_all([foreign_owner, foreign])
                session.commit()
                foreign_id = foreign.id
            response = await client.post(
                f"/workspaces/{foreign_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("checking.csv", CSV_BYTES, "text/csv")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 404
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_category_format_mismatch_does_not_store_the_file(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": "transaction_statement",
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": ("checking.pdf", b"%PDF-private", "application/pdf")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 400
    assert response.json()["code"] == "category_format_mismatch"
    assert list(tmp_path.rglob("*.*")) == []
```

- [ ] **Step 3: Write failing success-contract tests for both processors**

Add a local fake extractor and exact JSON assertions:

```python
from app.payslips.extraction import ExtractedText

PDF_BYTES = b"%PDF-synthetic-document-route"
PAY_TEXT = """Employer: Northstar Bicycle Works
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Net Pay: $3,700.00
Taxes: $900.00
Deductions: $400.00
"""


class RouteFakeExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(PAY_TEXT, "embedded_text")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category_key,filename,content_type,body,next_label,path_fragment,model",
    [
        (
            "transaction_statement",
            "checking.csv",
            "text/csv",
            CSV_BYTES,
            "Map columns",
            "/imports/",
            ImportJob,
        ),
        (
            "payslip",
            "pay.pdf",
            "application/pdf",
            PDF_BYTES,
            "Review payslip",
            "/payslips/",
            Payslip,
        ),
    ],
)
async def test_supported_document_returns_its_existing_review_destination(
    tmp_path: Path,
    category_key: str,
    filename: str,
    content_type: str,
    body: bytes,
    next_label: str,
    path_fragment: str,
    model: type,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.payslip_extractor = RouteFakeExtractor()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/document-uploads",
                data={
                    "category_key": category_key,
                    "retention_choice": "retain",
                    "csrf_token": token,
                },
                files={"document": (filename, body, content_type)},
            )
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(model)) == 1
            uploaded = session.scalar(select(UploadedFile))
            assert uploaded is not None
            assert uploaded.retention_choice == "retain"
    finally:
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"] == "Ready for review."
    assert payload["next_label"] == next_label
    assert path_fragment in payload["next_url"]
```

Add one malformed CSV case that asserts the parser's stable `code` and safe `message` are returned without a file or record.

- [ ] **Step 4: Run the API tests to verify they fail**

Run:

```powershell
uv run pytest tests/documents/test_routes.py -v
```

Expected: requests return 404 because the document router is not registered.

- [ ] **Step 5: Implement the synchronous one-file dispatcher**

Create `app/documents/routes.py` with these helpers and route. Keep the endpoint as `def`, not `async def`, so PDF extraction stays in FastAPI's worker thread just like the existing payslip route:

```python
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import require_current_user
from app.core.middleware import require_csrf
from app.db.models import User, Workspace
from app.db.session import get_db
from app.documents.catalog import DocumentUploadValidationError, validate_processable_upload
from app.documents.types import DocumentProcessResult
from app.imports.parser import CsvValidationError
from app.imports.service import ImportStateError, create_csv_import
from app.imports.storage import LocalUploadStore, UploadStorageError
from app.payslips.extraction import DocumentExtractionError, DocumentExtractor
from app.payslips.service import PayslipImportError, create_payslip_import
from app.payslips.storage import PayslipStorageError, PayslipUploadStore
from app.workspaces.dependencies import require_workspace

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["documents"])


def _error(code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "message": message})


def _process_csv(
    session: Session,
    store: LocalUploadStore,
    workspace: Workspace,
    document: UploadFile,
    retention_choice: str,
) -> DocumentProcessResult:
    result = create_csv_import(session, store, workspace, document.file, retention_choice)
    if result.kind == "already_committed":
        destination = f"/workspaces/{workspace.id}/transactions?already_imported=1"
        label = "View transactions"
    elif result.job.status == "reviewing":
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/review"
        label = "Review transactions"
    else:
        destination = f"/workspaces/{workspace.id}/imports/{result.job.id}/mapping"
        label = "Map columns"
    return DocumentProcessResult("Ready for review.", destination, label)


def _process_payslip(
    session: Session,
    store: PayslipUploadStore,
    extractor: DocumentExtractor,
    workspace: Workspace,
    document: UploadFile,
    retention_choice: str,
) -> DocumentProcessResult:
    suffix = Path(document.filename or "").suffix.casefold()
    payslip = create_payslip_import(
        session, store, extractor, workspace, document.file, suffix, retention_choice
    )
    return DocumentProcessResult(
        "Ready for review.",
        f"/workspaces/{workspace.id}/payslips/{payslip.id}/review",
        "Review payslip",
    )
```

The POST route must call `validate_processable_upload()` before either helper, use `request.app.state.upload_store`, `payslip_store`, and `payslip_extractor`, catch the exact existing validation/storage exceptions, and return `result.as_payload()`:

```python
@router.post("/document-uploads", dependencies=[Depends(require_csrf)])
def process_document_upload(
    request: Request,
    document: Annotated[UploadFile, File()],
    category_key: Annotated[str, Form()],
    retention_choice: Annotated[str, Form()],
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> JSONResponse:
    try:
        category = validate_processable_upload(
            category_key, document.filename or "", document.content_type
        )
        if category.processor == "csv_import":
            result = _process_csv(
                session, request.app.state.upload_store, workspace, document, retention_choice
            )
        else:
            assert category.processor == "payslip"
            result = _process_payslip(
                session,
                request.app.state.payslip_store,
                request.app.state.payslip_extractor,
                workspace,
                document,
                retention_choice,
            )
    except DocumentUploadValidationError as exc:
        return _error(exc.code, exc.message)
    except (
        CsvValidationError,
        UploadStorageError,
        ImportStateError,
        DocumentExtractionError,
        PayslipStorageError,
        PayslipImportError,
    ) as exc:
        return _error(exc.code, exc.message)
    return JSONResponse(result.as_payload())
```

The `else` branch is safe because the validator returns only the two processor literals; add `assert category.processor == "payslip"` before `_process_payslip` so type narrowing and future processor additions fail closed.

- [ ] **Step 6: Register the router and rerun the API tests**

Update `app/main.py`:

```python
from app.documents.routes import router as document_router

# after category_router and before import_router
application.include_router(document_router)
```

Run:

```powershell
uv run pytest tests/documents/test_routes.py tests/test_import_routes.py tests/payslips/test_routes.py -v
uv run ruff check app/documents app/main.py tests/documents tests/route_helpers.py
```

Expected: document API tests and both legacy route suites pass.

- [ ] **Step 7: Commit the dispatch API**

```powershell
git add app/documents/routes.py app/main.py tests/route_helpers.py tests/documents/test_routes.py
git commit -m "feat: route supported document uploads"
```

---

### Task 4: Render the unified upload page and workspace entry point

**Files:**
- Modify: `app/documents/routes.py`
- Create: `app/templates/documents/upload.html`
- Modify: `app/templates/workspace_detail.html:18-30`
- Create: `tests/documents/test_page.py`
- Modify: `tests/test_auth_routes.py:440`

**Interfaces:**
- Consumes: `client_catalog()`, `MAX_QUEUE_FILES`, current workspace/auth dependencies, and existing fallback routes.
- Produces: `GET /workspaces/{workspace_id}/documents/new` and the semantic DOM IDs consumed by `document-upload.js` in Task 5.

- [ ] **Step 1: Write failing page and navigation tests**

Create `tests/documents/test_page.py`:

```python
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Workspace
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_unified_document_page_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/documents/new", follow_redirects=False)
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_unified_document_page_exposes_manual_queue_contract(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.get(f"/workspaces/{workspace_id}/documents/new")
            workspace_page = await client.get(f"/workspaces/{workspace_id}")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert 'id="document-files"' in response.text
    assert 'multiple' in response.text
    assert 'id="document-queue-body"' in response.text
    assert 'id="document-category-config"' in response.text
    assert '"retirement_401k_statement"' in response.text
    assert 'value="delete_after_import" checked' in response.text
    assert f'/workspaces/{workspace_id}/imports/new' in response.text
    assert f'/workspaces/{workspace_id}/payslips/new' in response.text
    assert f'/workspaces/{workspace_id}/documents/new' in workspace_page.text
    assert "Upload documents" in workspace_page.text
```

Update the assertion in `tests/test_auth_routes.py` from `Import CSV` to `Upload documents`.

- [ ] **Step 2: Run the page tests to verify they fail**

Run:

```powershell
uv run pytest tests/documents/test_page.py tests/test_auth_routes.py -k "document or workspace_detail" -v
```

Expected: the unified GET returns 404 and the workspace assertion fails.

- [ ] **Step 3: Add the authorized GET route and client configuration**

Add Jinja templates and this route to `app/documents/routes.py`:

```python
from pathlib import Path

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.documents.catalog import MAX_QUEUE_FILES, client_catalog

templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/documents/new", response_class=HTMLResponse)
async def new_document_upload(
    request: Request,
    user: Annotated[User, Depends(require_current_user)],
    workspace: Annotated[Workspace, Depends(require_workspace)],
) -> HTMLResponse:
    settings = request.app.state.settings
    client_config = {
        "max_files": MAX_QUEUE_FILES,
        "endpoint": f"/workspaces/{workspace.id}/document-uploads",
        "categories": client_catalog(
            max_csv_bytes=settings.max_csv_upload_bytes,
            max_payslip_bytes=settings.max_payslip_upload_bytes,
        ),
    }
    return templates.TemplateResponse(
        request=request,
        name="documents/upload.html",
        context={
            "request": request,
            "current_user": user,
            "workspace": workspace,
            "csrf_token": request.state.csrf_token,
            "client_config": client_config,
        },
    )
```

In the template, serialize `client_config` with Jinja's `tojson` filter inside a JSON script node, then parse its `.textContent` in JavaScript. Do not concatenate JSON into markup manually.

- [ ] **Step 4: Create the semantic upload shell**

Create `app/templates/documents/upload.html` with these required elements and exact IDs:

```html
{% extends "base.html" %}

{% block title %}Upload documents · {{ workspace.name }}{% endblock %}

{% block content %}
  <section class="page-heading">
    <p class="eyebrow">{{ workspace.name }}</p>
    <h1>Upload documents</h1>
    <p>Add up to 10 files, choose every document category, then review the queue before processing.</p>
  </section>

  <form id="document-upload-form" class="panel document-upload" novalidate>
    <input id="document-csrf-token" type="hidden" value="{{ csrf_token }}">
    <button id="document-drop-zone" class="document-drop-zone" type="button" aria-describedby="document-file-help">
      <strong>Drop files here or browse</strong>
      <span id="document-file-help">CSV, PDF, PNG, or JPEG · multiple files supported</span>
    </button>
    <input id="document-files" class="visually-hidden" type="file" multiple accept=".csv,.pdf,.png,.jpg,.jpeg,text/csv,application/pdf,image/png,image/jpeg">

    <div id="document-page-alert" class="alert" role="alert" hidden></div>
    <p id="document-live-status" class="visually-hidden" aria-live="polite"></p>

    <div class="table-wrap document-queue-wrap" hidden>
      <table class="document-queue">
        <thead><tr><th scope="col">File</th><th scope="col">Document category</th><th scope="col">Status</th><th scope="col"><span class="visually-hidden">Actions</span></th></tr></thead>
        <tbody id="document-queue-body"></tbody>
      </table>
    </div>

    <fieldset class="document-retention">
      <legend>After each successful import or confirmation</legend>
      <label><input type="radio" name="retention_choice" value="delete_after_import" checked> Delete each raw document</label>
      <label><input type="radio" name="retention_choice" value="retain"> Retain each raw document privately</label>
    </fieldset>

    <button id="process-documents" class="button" type="submit" disabled>Process 0 files</button>
  </form>

  <noscript>
    <section class="notice">
      <p>JavaScript is required for a multi-file queue. You can still use the individual upload pages.</p>
      <a href="/workspaces/{{ workspace.id }}/imports/new">Import a transaction CSV</a>
      <a href="/workspaces/{{ workspace.id }}/payslips/new">Import a payslip</a>
    </section>
  </noscript>

  <script id="document-category-config" type="application/json">{{ client_config | tojson }}</script>
{% endblock %}
```

Add `.visually-hidden` to `styles.css` in Task 5; until then the semantic structure remains testable.

- [ ] **Step 5: Make unified upload the primary workspace action**

Replace the two import buttons in `app/templates/workspace_detail.html` with one leading document panel, while keeping the view links:

```html
<section class="panel">
  <h2>Documents</h2>
  <p>Upload transaction statements and payslips together, choose each category, then review every supported file.</p>
  <a class="button" href="/workspaces/{{ workspace.id }}/documents/new">Upload documents</a>
  <p class="muted">CSV transaction statements and PDF/image payslips are supported now. Other account-statement processors are planned.</p>
</section>

<section class="panel">
  <h2>Transactions</h2>
  <a href="/workspaces/{{ workspace.id }}/transactions">View transactions</a>
</section>

<section class="panel">
  <h2>Income</h2>
  <a href="/workspaces/{{ workspace.id }}/income">View confirmed income</a>
</section>
```

- [ ] **Step 6: Run page, auth, and template regressions**

Run:

```powershell
uv run pytest tests/documents/test_page.py tests/test_auth_routes.py tests/test_import_routes.py tests/payslips/test_routes.py -v
uv run ruff check app/documents/routes.py tests/documents/test_page.py
```

Expected: the unified page is authorized, fallback links remain present, and legacy upload pages still pass.

- [ ] **Step 7: Commit the unified entry point**

```powershell
git add app/documents/routes.py app/templates/documents/upload.html app/templates/workspace_detail.html tests/documents/test_page.py tests/test_auth_routes.py
git commit -m "feat: add unified document upload page"
```

---

### Task 5: Build and test the accessible browser queue

**Files:**
- Modify: `pyproject.toml:19-26`
- Modify: `uv.lock`
- Modify: `.github/workflows/ci.yml:19-29`
- Create: `tests/documents/conftest.py`
- Create: `tests/documents/test_upload_queue_browser.py`
- Create: `app/static/document-upload.js`
- Modify: `app/templates/documents/upload.html`
- Modify: `app/static/styles.css:172-255`

**Interfaces:**
- Consumes: the JSON configuration and DOM IDs from Task 4.
- Produces: a browser-only `Map` of queued `File` objects; ready-state calculation; duplicate, limit, removal, picker, and drag/drop behavior; responsive accessible queue presentation.

- [ ] **Step 1: Add the Python Playwright harness and Chromium CI install**

Run:

```powershell
uv add --dev "pytest-playwright>=0.7.0"
uv run playwright install chromium
```

Add the CI browser install immediately after `uv sync`:

```yaml
      - run: uv sync --all-groups --locked
      - name: Install Playwright Chromium
        run: uv run playwright install --with-deps chromium
```

- [ ] **Step 2: Add a live FastAPI and authenticated browser fixture**

Create `tests/documents/conftest.py`. Reuse `build_route_test_app`, run it on a free loopback port with Uvicorn, and sign in through the existing fake OAuth boundary:

```python
import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import BrowserContext, Page
from sqlalchemy import select

from app.db.models import Workspace
from app.payslips.extraction import ExtractedText
from tests.route_helpers import build_route_test_app

PDF_BYTES = b"%PDF-synthetic-browser"
PAY_TEXT = """Employer: Northstar Bicycle Works
Pay Date: 2026-07-20
Gross Pay: $5,000.00
Net Pay: $3,700.00
Taxes: $900.00
Deductions: $400.00
"""


class BrowserFakeExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        assert data == PDF_BYTES
        assert suffix == ".pdf"
        return ExtractedText(PAY_TEXT, "embedded_text")


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


@pytest.fixture
def live_document_app(tmp_path: Path) -> Generator[tuple[str, object], None, None]:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.payslip_extractor = BrowserFakeExtractor()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", factory
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        engine.dispose()


@pytest.fixture
def signed_in_upload_page(
    page: Page, context: BrowserContext, live_document_app: tuple[str, object]
) -> tuple[Page, int]:
    base_url, factory = live_document_app
    page.goto(base_url)
    csrf = page.locator('input[name="csrf_token"]').first.get_attribute("value")
    assert csrf
    started = context.request.post(
        f"{base_url}/auth/google", form={"csrf_token": csrf}, max_redirects=0
    )
    assert started.status == 302
    callback = context.request.get(f"{base_url}/auth/google/callback", max_redirects=0)
    assert callback.status == 303
    with factory() as session:
        workspace_id = session.scalar(select(Workspace.id))
        assert workspace_id is not None
    page.goto(f"{base_url}/workspaces/{workspace_id}/documents/new")
    return page, workspace_id
```

- [ ] **Step 3: Write failing picker, duplicate, category, limit, and removal tests**

Create `tests/documents/test_upload_queue_browser.py`:

```python
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"


def payload(name: str, mime_type: str, body: bytes) -> dict[str, object]:
    return {"name": name, "mimeType": mime_type, "buffer": body}


def test_picker_adds_multiple_manual_category_rows_and_removes_one(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    page.locator("#document-files").set_input_files(
        [
            payload("checking.csv", "text/csv", CSV_BYTES),
            payload("pay.pdf", "application/pdf", b"%PDF-synthetic"),
        ]
    )

    rows = page.locator("#document-queue-body tr")
    expect(rows).to_have_count(2)
    expect(rows.nth(0).locator("select")).to_have_value("")
    expect(rows.nth(1).locator("select")).to_have_value("")
    expect(page.locator("#process-documents")).to_be_disabled()

    rows.nth(0).locator("select").select_option("transaction_statement")
    expect(page.locator("#process-documents")).to_have_text("Process 1 file")
    rows.nth(1).get_by_role("button", name="Remove pay.pdf").click()
    expect(rows).to_have_count(1)


def test_same_file_is_suppressed_and_queue_is_limited_to_ten(
    signed_in_upload_page: tuple[Page, int], tmp_path: Path,
) -> None:
    page, _ = signed_in_upload_page
    same = tmp_path / "checking.csv"
    same.write_bytes(CSV_BYTES)
    page.locator("#document-files").set_input_files([same])
    page.locator("#document-files").set_input_files([same])
    expect(page.locator("#document-queue-body tr")).to_have_count(1)
    expect(page.locator("#document-live-status")).to_contain_text("already in the queue")

    page.locator("#document-files").set_input_files(
        [payload(f"file-{number}.csv", "text/csv", CSV_BYTES) for number in range(2, 12)]
    )
    expect(page.locator("#document-queue-body tr")).to_have_count(10)
    expect(page.locator("#document-live-status")).to_contain_text("10-file limit")
```

Add a drag/drop test that runs this page-side event and verifies the new row:

```python
page.evaluate(
    """({name, type, body}) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([body], name, {type, lastModified: 1722470400000}));
      document.querySelector('#document-drop-zone').dispatchEvent(
        new DragEvent('drop', {dataTransfer: transfer, bubbles: true, cancelable: true})
      );
    }""",
    {"name": "dropped.csv", "type": "text/csv", "body": "Date,Description,Amount\n"},
)
expect(page.get_by_text("dropped.csv")).to_be_visible()
```

Add this eligibility test for mismatch, processor-less, unlisted, and oversized rows:

```python
def test_manual_categories_control_file_eligibility(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    page.locator("#document-files").set_input_files(
        [
            payload("wrong.pdf", "application/pdf", b"%PDF-wrong"),
            payload("future.pdf", "application/pdf", b"%PDF-future"),
            payload("unlisted.pdf", "application/pdf", b"%PDF-unlisted"),
            payload("large.csv", "text/csv", b"x" * (5 * 1024 * 1024 + 1)),
            payload("notes.txt", "text/plain", b"not supported"),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("brokerage_statement")
    rows.nth(2).locator("select").select_option("unlisted")
    rows.nth(3).locator("select").select_option("transaction_statement")

    expect(rows.nth(0).locator(".document-status")).to_contain_text("does not support")
    expect(rows.nth(1).locator(".document-status")).to_contain_text("not available yet")
    expect(rows.nth(2).locator(".document-status")).to_contain_text("Remove this file")
    expect(rows.nth(3).locator(".document-status")).to_contain_text("5 MiB limit")
    expect(rows.nth(4).locator(".document-status")).to_contain_text("Choose a CSV, PDF, PNG, or JPEG")
    expect(page.locator("#process-documents")).to_be_disabled()
```

- [ ] **Step 4: Run the browser tests to verify they fail**

Run:

```powershell
uv run pytest tests/documents/test_upload_queue_browser.py -v
```

Expected: rows remain empty because the page has no queue script.

- [ ] **Step 5: Implement local queue state and accessible DOM updates**

Create `app/static/document-upload.js` as one strict IIFE. Use these exact state fields and transition names so later request tests can reason about them:

```javascript
(() => {
  "use strict";

  const configNode = document.getElementById("document-category-config");
  const form = document.getElementById("document-upload-form");
  if (!configNode || !form) return;

  const config = JSON.parse(configNode.textContent);
  const categoryByKey = new Map(config.categories.map((category) => [category.key, category]));
  const queue = new Map();
  const fileInput = document.getElementById("document-files");
  const dropZone = document.getElementById("document-drop-zone");
  const queueBody = document.getElementById("document-queue-body");
  const queueWrap = document.querySelector(".document-queue-wrap");
  const processButton = document.getElementById("process-documents");
  const liveStatus = document.getElementById("document-live-status");
  let nextId = 1;

  const suffixOf = (name) => {
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot).toLowerCase();
  };
  const signatureOf = (file) => [file.name, file.size, file.lastModified, file.type].join("|");
  const plural = (count) => `${count} ${count === 1 ? "file" : "files"}`;

  function compatibility(item) {
    const suffix = suffixOf(item.file.name);
    if (![".csv", ".pdf", ".png", ".jpg", ".jpeg"].includes(suffix)) {
      return {state: "invalid", message: "Choose a CSV, PDF, PNG, or JPEG file."};
    }
    if (!item.categoryKey) return {state: "needs-category", message: "Choose a category."};
    const category = categoryByKey.get(item.categoryKey);
    if (!category || category.key === "unlisted") {
      return {state: "unsupported", message: "Remove this file or choose a supported category."};
    }
    if (!category.supported) {
      return {state: "unsupported", message: "Recognized, but processing is not available yet."};
    }
    if (!category.accepted_suffixes.includes(suffix)) {
      return {state: "invalid", message: `${category.label} does not support ${suffix || "this format"}.`};
    }
    if (item.file.size > category.max_bytes) {
      return {state: "invalid", message: `This file exceeds the ${Math.floor(category.max_bytes / 1048576)} MiB limit.`};
    }
    return {state: "ready", message: "Ready to process."};
  }
```

Add these queue functions below `compatibility()`; all user-controlled names and labels enter the DOM through `textContent`:

```javascript
  const announce = (message) => { liveStatus.textContent = message; };
  const humanSize = (bytes) => bytes < 1048576
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1048576).toFixed(1)} MB`;

  function refreshBatch() {
    const readyCount = [...queue.values()].filter((item) => item.state === "ready").length;
    processButton.textContent = `Process ${plural(readyCount)}`;
    processButton.disabled = readyCount === 0;
    queueWrap.hidden = queue.size === 0;
  }

  function refreshItem(item) {
    const result = compatibility(item);
    item.state = result.state;
    item.message = result.message;
    item.status.textContent = result.message;
    item.status.dataset.state = result.state;
    refreshBatch();
  }

  function removeItem(id) {
    const item = queue.get(id);
    if (!item) return;
    const nextRow = item.row.nextElementSibling;
    const previousRow = item.row.previousElementSibling;
    const filename = item.file.name;
    queue.delete(id);
    item.row.remove();
    item.file = null;
    refreshBatch();
    const focusTarget = nextRow?.querySelector("select")
      || previousRow?.querySelector("select")
      || dropZone;
    focusTarget.focus();
    announce(`${filename} removed. ${plural(queue.size)} remain in the queue.`);
  }

  function createRow(item) {
    const row = document.createElement("tr");
    row.dataset.itemId = item.id;

    const fileCell = document.createElement("td");
    const filename = document.createElement("strong");
    const metadata = document.createElement("span");
    filename.textContent = item.file.name;
    metadata.className = "muted visually-explained";
    metadata.textContent = humanSize(item.file.size);
    fileCell.append(filename, metadata);

    const categoryCell = document.createElement("td");
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Document category for ${item.file.name}`);
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = "Choose a category";
    select.append(prompt);
    for (const category of config.categories) {
      const option = document.createElement("option");
      option.value = category.key;
      option.textContent = category.label;
      select.append(option);
    }
    select.addEventListener("change", () => {
      item.categoryKey = select.value;
      refreshItem(item);
    });
    categoryCell.append(select);

    const statusCell = document.createElement("td");
    const statusText = document.createElement("span");
    statusText.className = "document-status";
    statusCell.append(statusText);

    const actionCell = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "document-remove";
    remove.setAttribute("aria-label", `Remove ${item.file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeItem(item.id));
    actionCell.append(remove);

    row.append(fileCell, categoryCell, statusCell, actionCell);
    item.row = row;
    item.select = select;
    item.status = statusText;
    item.actionCell = actionCell;
    item.remove = remove;
    queueBody.append(row);
    refreshItem(item);
  }

  function addFiles(files) {
    let added = 0;
    let duplicate = false;
    let limited = false;
    const signatures = new Set([...queue.values()].map((item) => item.signature));
    for (const file of files) {
      const signature = signatureOf(file);
      if (signatures.has(signature)) {
        duplicate = true;
        continue;
      }
      if (queue.size >= config.max_files) {
        limited = true;
        break;
      }
      const item = {
        id: `document-${nextId++}`,
        signature,
        file,
        categoryKey: "",
        state: "needs-category",
        message: "Choose a category.",
        row: null,
      };
      queue.set(item.id, item);
      signatures.add(signature);
      createRow(item);
      added += 1;
    }
    if (limited) announce(`Some files were not added because the queue has a ${config.max_files}-file limit.`);
    else if (duplicate) announce(`A selected file is already in the queue. ${plural(added)} added.`);
    else announce(`${plural(added)} added. ${plural(queue.size)} in the queue.`);
    fileInput.value = "";
    refreshBatch();
  }

  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => addFiles(fileInput.files));
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  }
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-dragging"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
    addFiles(event.dataTransfer.files);
  });
  const preventSubmit = (event) => event.preventDefault();
  form.addEventListener("submit", preventSubmit);
```

Use JSDoc or a small local typedef so `file` is understood as `File | null` after removal. No queue function may dereference `item.file` after `removeItem()` deletes that item from the map.

- [ ] **Step 6: Load the script and add responsive queue styles**

Append to `app/templates/documents/upload.html`:

```html
<script src="{{ url_for('static', path='/document-upload.js') }}" defer></script>
```

Add styles that reuse the current green palette and include these behavior selectors:

```css
.visually-hidden {
  height: 1px;
  margin: -1px;
  overflow: hidden;
  padding: 0;
  position: absolute;
  width: 1px;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.document-upload { display: grid; gap: 1rem; }
.document-drop-zone { background: #eef7f0; border: 2px dashed #1d6a42; border-radius: 0.75rem; color: #173f2b; cursor: pointer; padding: 2rem 1rem; text-align: center; width: 100%; }
.document-drop-zone span { color: #667268; display: block; font-size: 0.9rem; margin-top: 0.25rem; }
.document-drop-zone.is-dragging { background: #e2f2e5; border-style: solid; }
.document-drop-zone:focus-visible { outline: 3px solid #266340; outline-offset: 3px; }
.document-queue select { min-width: 13rem; }
.document-remove { background: transparent; border: 0; color: #92351d; cursor: pointer; font-size: 1.4rem; }
.document-status[data-state="ready"], .document-status[data-state="complete"] { color: #176338; }
.document-status[data-state="invalid"], .document-status[data-state="failed"] { color: #92351d; }

@media (max-width: 42rem) {
  .document-queue thead { display: none; }
  .document-queue tr { border-bottom: 1px solid #dbe3d7; display: grid; gap: 0.5rem; padding: 0.75rem 0; }
  .document-queue td { border: 0; padding: 0; }
  .document-queue select { min-width: 0; width: 100%; }
}
```

- [ ] **Step 7: Run queue tests at desktop and narrow viewport**

Add this exact responsive test:

```python
@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 800}, {"width": 390, "height": 844}])
def test_queue_has_no_page_overflow_at_supported_widths(
    signed_in_upload_page: tuple[Page, int], viewport: dict[str, int]
) -> None:
    page, _ = signed_in_upload_page
    page.set_viewport_size(viewport)
    page.locator("#document-files").set_input_files(
        [payload("checking.csv", "text/csv", CSV_BYTES), payload("pay.pdf", "application/pdf", b"%PDF")]
    )
    assert page.evaluate("document.documentElement.scrollWidth") == page.evaluate(
        "document.documentElement.clientWidth"
    )
```

Then run:

```powershell
uv run pytest tests/documents/test_upload_queue_browser.py -k "picker or same_file or drag" -v
uv run ruff check tests/documents/conftest.py tests/documents/test_upload_queue_browser.py
```

Expected: queue behavior passes at both sizes with no horizontal page overflow (`document.documentElement.scrollWidth == document.documentElement.clientWidth`).

- [ ] **Step 8: Commit the queue and browser harness**

```powershell
git add pyproject.toml uv.lock .github/workflows/ci.yml tests/documents/conftest.py tests/documents/test_upload_queue_browser.py app/static/document-upload.js app/templates/documents/upload.html app/static/styles.css
git commit -m "feat: add multi-file drag and drop queue"
```

---

### Task 6: Process ready rows sequentially with partial success and retry

**Files:**
- Modify: `app/static/document-upload.js`
- Modify: `tests/documents/test_upload_queue_browser.py`

**Interfaces:**
- Consumes: `POST /workspaces/{workspace_id}/document-uploads`, CSRF token, retention radio, and `DocumentProcessResult.as_payload()`.
- Produces: `processQueue()`, `processItem()`, completed next-step anchors, per-row retry, and page-level session/CSRF stop behavior.

- [ ] **Step 1: Write a failing real-backend mixed-batch test**

Add this test using the same synthetic PDF bytes expected by `BrowserFakeExtractor`:

```python
PDF_BYTES = b"%PDF-synthetic-browser"


def test_ready_files_process_sequentially_and_keep_independent_results(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url)
        if request.method == "POST" and request.url.endswith("/document-uploads")
        else None,
    )
    page.locator("#document-files").set_input_files(
        [
            payload("broken.csv", "text/csv", b"not,a,rectangular\n1,2\n"),
            payload("pay.pdf", "application/pdf", PDF_BYTES),
            payload("future.pdf", "application/pdf", b"private future statement"),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("payslip")
    rows.nth(2).locator("select").select_option("brokerage_statement")

    page.locator("#process-documents").click()

    expect(rows.nth(0).locator(".document-status")).to_contain_text("CSV")
    expect(rows.nth(1).get_by_role("link", name="Review payslip")).to_be_visible()
    expect(rows.nth(2).locator(".document-status")).to_contain_text("not available yet")
    assert len(requests) == 2
```

Add this valid mixed-processor and retention test:

```python
from sqlalchemy import select

from app.db.models import UploadedFile


def test_valid_csv_and_payslip_produce_locked_review_links_and_keep_retention(
    signed_in_upload_page: tuple[Page, int], live_document_app: tuple[str, object]
) -> None:
    page, _ = signed_in_upload_page
    _, factory = live_document_app
    page.locator("#document-files").set_input_files(
        [
            payload("checking.csv", "text/csv", CSV_BYTES),
            payload("pay.pdf", "application/pdf", PDF_BYTES),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("payslip")
    page.get_by_label("Retain each raw document privately").check()
    page.locator("#process-documents").click()

    expect(rows.nth(0).get_by_role("link", name="Map columns")).to_be_visible()
    expect(rows.nth(1).get_by_role("link", name="Review payslip")).to_be_visible()
    expect(rows.nth(0).locator("select")).to_be_disabled()
    expect(rows.nth(1).locator("select")).to_be_disabled()
    with factory() as session:
        uploads = session.scalars(select(UploadedFile).order_by(UploadedFile.id)).all()
        assert [upload.retention_choice for upload in uploads] == ["retain", "retain"]
```

- [ ] **Step 2: Write failing retry and expired-session stop tests**

Use Playwright routing for a one-time network abort:

```python
def test_network_failure_can_retry_only_the_failed_row(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    aborted = False

    def fail_once(route) -> None:
        nonlocal aborted
        if not aborted:
            aborted = True
            route.abort()
        else:
            route.continue_()

    page.route("**/document-uploads", fail_once)
    page.locator("#document-files").set_input_files(
        payload("checking.csv", "text/csv", CSV_BYTES)
    )
    row = page.locator("#document-queue-body tr")
    row.locator("select").select_option("transaction_statement")
    page.locator("#process-documents").click()
    expect(row.get_by_role("button", name="Retry checking.csv")).to_be_visible()
    row.get_by_role("button", name="Retry checking.csv").click()
    expect(row.get_by_role("link", name="Map columns")).to_be_visible()
```

Add this CSRF stop test:

```python
def test_expired_csrf_stops_before_the_second_ready_file(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url)
        if request.method == "POST" and request.url.endswith("/document-uploads")
        else None,
    )
    page.locator("#document-files").set_input_files(
        [
            payload("first.csv", "text/csv", CSV_BYTES),
            payload("second.csv", "text/csv", CSV_BYTES.replace(b"Market", b"Store")),
        ]
    )
    rows = page.locator("#document-queue-body tr")
    rows.nth(0).locator("select").select_option("transaction_statement")
    rows.nth(1).locator("select").select_option("transaction_statement")
    page.locator("#document-csrf-token").evaluate(
        "element => { element.value = 'expired-or-invalid'; }"
    )
    page.locator("#process-documents").click()

    expect(page.locator("#document-page-alert")).to_be_visible()
    expect(rows.nth(1).locator(".document-status")).to_have_text("Ready to process.")
    assert len(requests) == 1
```

- [ ] **Step 3: Run the new browser tests to verify they fail**

Run:

```powershell
uv run pytest tests/documents/test_upload_queue_browser.py -k "sequentially or network_failure or expired" -v
```

Expected: clicking Process submits the inert form or performs no request because processing functions do not exist.

- [ ] **Step 4: Implement sequential per-item requests and safe result links**

Extend `app/static/document-upload.js` with these exact rules:

```javascript
  const csrfToken = document.getElementById("document-csrf-token");
  const pageAlert = document.getElementById("document-page-alert");
  let processingQueue = false;

  function setItemState(item, state, message) {
    item.state = state;
    item.message = message;
    item.status.textContent = message;
    item.status.dataset.state = state;
    refreshBatch();
  }

  function setPendingControls(item, disabled) {
    item.select.disabled = disabled;
    item.remove.disabled = disabled;
    item.row.setAttribute("aria-busy", disabled ? "true" : "false");
  }

  function restoreRemoveAction(item) {
    item.actionCell.replaceChildren(item.remove);
    item.remove.disabled = false;
  }

  function markRetryable(item, message) {
    setPendingControls(item, false);
    setItemState(item, "retryable", message);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Retry";
    retry.setAttribute("aria-label", `Retry ${item.file.name}`);
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      await processItem(item);
      refreshBatch();
    });
    item.actionCell.replaceChildren(retry, item.remove);
  }

  function completeItem(item, href, label, message) {
    setPendingControls(item, false);
    setItemState(item, "complete", message || "Ready for review.");
    item.select.disabled = true;
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    item.actionCell.replaceChildren(link);
    item.file = null;
  }

  function showSessionAlert() {
    pageAlert.textContent = "Your session could not be verified. Reload or sign in, then process the remaining files.";
    pageAlert.hidden = false;
  }

  async function processItem(item) {
    setPendingControls(item, true);
    setItemState(item, "processing", "Processing…");
    announce(`Processing ${item.file.name}`);

    const body = new FormData();
    body.append("document", item.file, item.file.name);
    body.append("category_key", item.categoryKey);
    body.append("retention_choice", form.elements.retention_choice.value);
    body.append("csrf_token", csrfToken.value);

    let response;
    try {
      response = await fetch(config.endpoint, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrfToken.value},
      });
    } catch (_error) {
      markRetryable(item, "The upload was interrupted. Retry this file.");
      return "continue";
    }

    const contentType = response.headers.get("content-type") || "";
    if (response.redirected || response.status === 401 || response.status === 403) {
      setPendingControls(item, false);
      restoreRemoveAction(item);
      setItemState(item, "ready", "Waiting for a verified session.");
      showSessionAlert();
      return "stop";
    }

    if (!contentType.includes("application/json")) {
      markRetryable(item, "The server could not process this file. Retry it.");
      return "continue";
    }

    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      markRetryable(item, "The server returned an invalid result. Retry this file.");
      return "continue";
    }
    if (!response.ok || !payload.ok) {
      if (response.status >= 500) markRetryable(item, "The server could not process this file. Retry it.");
      else {
        setPendingControls(item, false);
        restoreRemoveAction(item);
        setItemState(item, "failed", payload.message || "This file could not be processed.");
      }
      return "continue";
    }

    const nextUrl = new URL(payload.next_url, window.location.origin);
    if (nextUrl.origin !== window.location.origin) {
      markRetryable(item, "The review link was invalid. Retry this file.");
      return "continue";
    }
    completeItem(item, `${nextUrl.pathname}${nextUrl.search}`, payload.next_label, payload.message);
    return "continue";
  }

  async function processQueue(event) {
    event.preventDefault();
    if (processingQueue) return;
    processingQueue = true;
    pageAlert.hidden = true;
    processButton.disabled = true;
    try {
      for (const item of queue.values()) {
        if (item.state !== "ready") continue;
        const action = await processItem(item);
        if (action === "stop") break;
      }
    } finally {
      processingQueue = false;
      refreshBatch();
    }
  }

  form.removeEventListener("submit", preventSubmit);
  form.addEventListener("submit", processQueue);
```

In Task 5's category `change` listener, call `restoreRemoveAction(item)` before `refreshItem(item)` so editing a retryable or failed category removes a stale retry button. Validation failures remain editable and do not get a blind retry button.

- [ ] **Step 5: Add processing-state accessibility and no-double-submit behavior**

Add these assertions to the valid and partial-success tests:

```python
expect(rows.nth(0)).to_have_attribute("aria-busy", "false")
expect(rows.nth(1)).to_have_attribute("aria-busy", "false")
expect(page.locator("#process-documents")).to_have_text("Process 0 files")
expect(page.locator("#process-documents")).to_be_disabled()
```

Add this focused no-double-submit test:

```python
def test_repeated_process_click_starts_only_one_request(
    signed_in_upload_page: tuple[Page, int],
) -> None:
    page, _ = signed_in_upload_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url)
        if request.method == "POST" and request.url.endswith("/document-uploads")
        else None,
    )
    page.locator("#document-files").set_input_files(
        payload("checking.csv", "text/csv", CSV_BYTES)
    )
    page.locator("#document-queue-body select").select_option("transaction_statement")
    page.locator("#process-documents").evaluate("button => { button.click(); button.click(); }")
    expect(page.get_by_role("link", name="Map columns")).to_be_visible()
    assert len(requests) == 1
```

- [ ] **Step 6: Run the complete browser and backend feature suite**

Run:

```powershell
uv run pytest tests/documents -v
uv run pytest tests/test_import_routes.py tests/test_csv_import_flow.py tests/payslips -v
uv run ruff check app/documents tests/documents
```

Ruff will ignore JavaScript content but still lint every Python path. Expected: all document browser/route tests and legacy processor suites pass.

- [ ] **Step 7: Commit sequential partial-success processing**

```powershell
git add app/static/document-upload.js tests/documents/test_upload_queue_browser.py
git commit -m "feat: process document queue sequentially"
```

---

### Task 7: Document the V1 workflow and run final verification

**Files:**
- Modify: `README.md:99-143,201-215,255-264`
- Verify: entire repository

**Interfaces:**
- Consumes: all completed feature interfaces and the remaining-processor roadmap in `docs/where-is-my-money-pr-breakdown.md`.
- Produces: contributor/user documentation that does not claim unsupported processors or AI classification.

- [ ] **Step 1: Update README usage and architecture text**

Replace the separate entry instructions with a leading `Upload documents` section that states:

```markdown
## Upload documents

1. Sign in, open a workspace, and select **Upload documents**.
2. Browse or drag and drop up to 10 CSV, PDF, PNG, or JPEG files.
3. Choose a document category for every file. V1 does not guess or preselect categories.
4. Remove unwanted files with **X**, choose one source-retention policy, and select **Process**.
5. Follow **Map columns** for transaction CSVs or **Review payslip** for PDF/image payslips.

Only transaction CSV and payslip processors are available in V1. 401(k),
brokerage/stocks, mortgage, loan, and other account statements remain in the
browser and are not uploaded. Their processors are tracked in PR 8b of
`docs/where-is-my-money-pr-breakdown.md`.
```

Keep the detailed CSV mapping and payslip confirmation subsections beneath it. Update the testing section to mention Chromium browser-flow coverage and the architecture tree to include `app/documents/`.

- [ ] **Step 2: Run formatting and static checks**

Run:

```powershell
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 3: Run the complete automated test suite**

Run:

```powershell
uv run playwright install chromium
uv run pytest
```

Expected: all existing and new tests pass; the document browser tests run under Chromium rather than being skipped.

- [ ] **Step 4: Verify migrations and application startup without changing tracked data**

Create a task-specific temporary database path and run:

```powershell
$documentVerifyDb = Join-Path ([System.IO.Path]::GetTempPath()) ("wimm-document-verify-" + [guid]::NewGuid() + ".db")
$env:DATABASE_URL = "sqlite:///" + ($documentVerifyDb -replace "\\", "/")
uv run alembic upgrade head
uv run pytest tests/test_app.py::test_health_check_returns_ok -v
Remove-Item Env:DATABASE_URL
Remove-Item -LiteralPath $documentVerifyDb -Force
```

Expected: migrations reach head and `/health` returns `{"status": "ok"}`. Only the explicitly named temporary database file is removed.

- [ ] **Step 5: Commit documentation and verified lockfile state**

```powershell
git add README.md pyproject.toml uv.lock .github/workflows/ci.yml
git commit -m "docs: explain unified document uploads"
```

- [ ] **Step 6: Confirm the branch is clean and summarize the processor boundary**

Run:

```powershell
git status --short
git log -7 --oneline
```

Expected: `git status --short` is empty. The handoff must state that only transaction CSV and payslip processors were implemented and link the remaining processor roadmap rather than claiming those categories work.

## Execution handoff

Plan complete. Execute it with one of these workflows:

1. **Subagent-Driven (recommended):** use `superpowers:subagent-driven-development`, dispatch one fresh implementation worker per task, and perform specification and quality reviews after every task.
2. **Inline Execution:** use `superpowers:executing-plans`, implement tasks in batches with review checkpoints in this session.
