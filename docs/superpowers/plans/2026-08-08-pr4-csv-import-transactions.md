# PR 4 Private CSV Imports and Transaction Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authorized workspace member privately upload, map, review, correct, deduplicate, and commit a CSV statement, then browse those transactions through workspace-scoped filters.

**Architecture:** Thin FastAPI routes consume PR 3's authenticated `User`, membership-authorized `Workspace`, and CSRF verifier. Focused pure-Python modules parse and normalize a small UTF-8 CSV deterministically; a local storage adapter holds the raw source until an explicit commit; one SQLAlchemy service atomically creates approved transactions. Transaction browsing uses a separately tested workspace-scoped query object.

**Tech Stack:** Python 3.12, FastAPI server-rendered forms, Jinja2, SQLAlchemy 2.0, Alembic, SQLite, Python `csv`/`decimal`/`hashlib`, pytest, HTTPX ASGI transport, Ruff.

## Global Constraints

- Start the production branch only after PR 3 is merged into `main`.
- Consume PR 3's signed session, workspace membership authorization, CSRF verifier, base template, and test override hooks; do not reimplement them.
- Every import, file, category, and transaction read/write is scoped to the authorized workspace.
- Return 404 for a resource that exists only in another workspace.
- Every state-changing form is CSRF-protected before file or database mutation.
- Default retention is `delete_after_import`; `retain` must be explicit.
- Raw sources are private and have no web download/preview endpoint.
- Accept only UTF-8/UTF-8-BOM CSV up to 5 MiB, 50 columns, 1,000 data rows, and 2,000 characters per field.
- Store amounts as signed integer cents and transaction dates as midnight UTC.
- Do not persist candidate transactions before explicit review/commit.
- Do not add automatic categorization rules, custom categories, account UI, non-CSV formats, background jobs, LangGraph, cloud storage, or JavaScript frameworks.
- Use only synthetic fixtures; never commit a real financial statement.

---

### Task 0: Audit merged PR 3 and create the execution branch

**Files:**
- Review: `app/auth/`
- Review: `app/workspaces/`
- Review: `app/templates/base.html`
- Review: `tests/conftest.py`
- Modify only if names differ: `docs/superpowers/specs/2026-08-08-pr4-csv-import-transactions-design.md`
- Modify only if names differ: `docs/superpowers/plans/2026-08-08-pr4-csv-import-transactions.md`

**Interfaces:**
- Consumes: merged PR 3 authentication, workspace authorization, CSRF, template context, and test fixtures.
- Produces: a checked-out `codex/pr-4-csv-import-transactions` branch and confirmed local names for the behavioral contract below.

- [ ] **Step 1: Confirm PR 3 is in merged main**

Run:

```powershell
git status --short --branch
git fetch origin
git switch main
git pull --ff-only
git log --oneline --decorate -8
```

Expected: the worktree is clean and `main` contains the PR 3 merge after
`a12aa57`. If PR 3 is absent, stop; all production tasks remain blocked.

- [ ] **Step 2: Create the production branch from that merge**

Run:

```powershell
git switch -c codex/pr-4-csv-import-transactions
```

Expected: `git branch --show-current` prints
`codex/pr-4-csv-import-transactions`.

- [ ] **Step 3: Verify the PR 3 contract by inspection**

Record the actual symbols that provide all of these behaviors:

```python
require_current_user(request, session) -> User
require_workspace(workspace_id, user, session) -> Workspace
async require_csrf(request) -> None
request.state.csrf_token: str
get_db() -> Generator[Session, None, None]
```

Confirm with PR 3 tests that unauthenticated HTML requests follow its sign-in
behavior, unauthorized workspace IDs return 404, owners and accepted members
have equal access, and dependency overrides do not invoke live Google OAuth.

- [ ] **Step 4: Reconcile names without weakening behavior**

If PR 3 uses different module/symbol names, update the two documentation files
with those exact names. Do not add a second session decoder, membership query,
or CSRF implementation. Review the diff and confirm only interface names and
import paths changed.

- [ ] **Step 5: Run the merged baseline**

Run:

```powershell
uv sync --all-groups
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
& .\.venv\Scripts\pytest.exe
```

Expected: every PR 3 check passes before PR 4's first red test. Do not continue
if the baseline is red.

---

### Task 1: Define import value types and validate column mappings

**Files:**
- Create: `app/imports/__init__.py`
- Create: `app/imports/types.py`
- Create: `app/imports/mapping.py`
- Create: `tests/test_import_mapping.py`

**Interfaces:**
- Consumes: only Python standard-library types.
- Produces: `CsvSourceRow`, `CsvDocument`, `ColumnMapping`,
  `NormalizedTransaction`, `FingerprintedTransaction`, `MappingValidationError`,
  `validate_mapping(headers, form) -> ColumnMapping`, and
  `mapping_from_json(headers, value) -> ColumnMapping`.

- [ ] **Step 1: Write the failing mapping tests**

Create `tests/test_import_mapping.py`:

```python
import pytest

from app.imports.mapping import MappingValidationError, validate_mapping


HEADERS = ("Date", "Description", "Amount", "Debit", "Credit")


def test_single_amount_mapping_is_typed() -> None:
    mapping = validate_mapping(
        HEADERS,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "invert",
        },
    )
    assert mapping.amount_mode == "single"
    assert mapping.amount_column == "Amount"
    assert mapping.debit_column is None
    assert mapping.amount_sign == "invert"


def test_split_mapping_requires_distinct_debit_and_credit() -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(
            HEADERS,
            {
                "date_column": "Date",
                "description_column": "Description",
                "amount_mode": "split",
                "debit_column": "Debit",
                "credit_column": "Debit",
                "date_format": "iso",
            },
        )
    assert error.value.field_errors == {
        "credit_column": "Debit and credit must use different columns."
    }


@pytest.mark.parametrize("date_format", ["auto", "yyyy/mm/dd", ""])
def test_unknown_date_format_is_rejected(date_format: str) -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(
            HEADERS,
            {
                "date_column": "Date",
                "description_column": "Description",
                "amount_mode": "single",
                "amount_column": "Amount",
                "date_format": date_format,
                "amount_sign": "as_is",
            },
        )
    assert "date_format" in error.value.field_errors


def test_mapping_rejects_a_header_used_twice() -> None:
    with pytest.raises(MappingValidationError) as error:
        validate_mapping(
            HEADERS,
            {
                "date_column": "Date",
                "description_column": "Date",
                "amount_mode": "single",
                "amount_column": "Amount",
                "date_format": "mdy",
                "amount_sign": "as_is",
            },
        )
    assert error.value.field_errors["description_column"] == (
        "Each field must use a different CSV column."
    )
```

- [ ] **Step 2: Run the tests and confirm the red phase**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_mapping.py -v
```

Expected: collection fails because `app.imports.mapping` does not exist.

- [ ] **Step 3: Add the focused immutable types**

In `app/imports/types.py`, define these signatures:

```python
from dataclasses import dataclass
from datetime import date
from typing import Literal

DateFormat = Literal["iso", "mdy", "dmy"]
AmountMode = Literal["single", "split"]
AmountSign = Literal["as_is", "invert"]


@dataclass(frozen=True)
class CsvSourceRow:
    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class CsvDocument:
    headers: tuple[str, ...]
    rows: tuple[CsvSourceRow, ...]
    delimiter: str


@dataclass(frozen=True)
class ColumnMapping:
    date_column: str
    description_column: str
    amount_mode: AmountMode
    amount_column: str | None
    debit_column: str | None
    credit_column: str | None
    date_format: DateFormat
    amount_sign: AmountSign

    def to_json(self) -> dict[str, str | None]:
        return {
            "date_column": self.date_column,
            "description_column": self.description_column,
            "amount_mode": self.amount_mode,
            "amount_column": self.amount_column,
            "debit_column": self.debit_column,
            "credit_column": self.credit_column,
            "date_format": self.date_format,
            "amount_sign": self.amount_sign,
        }


@dataclass(frozen=True)
class NormalizedTransaction:
    row_number: int
    transaction_date: date
    description: str
    normalized_merchant: str
    amount_cents: int


@dataclass(frozen=True)
class FingerprintedTransaction:
    transaction: NormalizedTransaction
    occurrence: int
    fingerprint: str
```

Implement `to_json` exactly as shown. In `mapping.py`, add
`mapping_from_json(headers, value) -> ColumnMapping`; it rejects unknown/missing
keys and calls `validate_mapping` so persisted JSON receives the same checks as
form data without creating a circular import from `types.py`.

- [ ] **Step 4: Implement minimal mapping validation**

In `app/imports/mapping.py`, define:

```python
class MappingValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Invalid CSV column mapping")
        self.field_errors = field_errors


def validate_mapping(headers: tuple[str, ...], form: Mapping[str, object]) -> ColumnMapping:
    """Validate exact headers, amount mode/sign, date format, and uniqueness."""
```

Accept only `single`/`split`, `iso`/`mdy`/`dmy`, and `as_is`/`invert`. Require
date and description plus either one amount or two distinct debit/credit
headers. Reject any selected header absent from `headers` and any logical field
collision. Return all discoverable field errors in one exception.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_mapping.py -v
& .\.venv\Scripts\ruff.exe check app\imports tests\test_import_mapping.py
git add app/imports tests/test_import_mapping.py
git commit -m "feat: validate CSV column mappings"
```

Expected: mapping tests and Ruff pass.

---

### Task 2: Parse bounded UTF-8 CSV documents

**Files:**
- Create: `app/imports/parser.py`
- Create: `tests/test_csv_parser.py`

**Interfaces:**
- Consumes: `CsvDocument` and `CsvSourceRow` from Task 1.
- Produces: `CsvValidationError(code, message)` and
  `parse_csv_bytes(data: bytes) -> CsvDocument`.

- [ ] **Step 1: Write parser happy-path and boundary tests**

Create `tests/test_csv_parser.py` with synthetic bytes:

```python
import pytest

from app.imports.parser import CsvValidationError, parse_csv_bytes


def test_parses_utf8_bom_and_semicolon_rows() -> None:
    document = parse_csv_bytes(
        "\ufeffDate;Description;Amount\n08/01/2026;Example Market;-12.34\n".encode()
    )
    assert document.headers == ("Date", "Description", "Amount")
    assert document.delimiter == ";"
    assert document.rows[0].row_number == 2
    assert document.rows[0].values["Amount"] == "-12.34"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty_file"),
        (b"\xff\xfeDate", "invalid_encoding"),
        (b"Date,Description\n2026-01-01,Hello\x00World", "nul_byte"),
        (b"Date,Date\n2026-01-01,2026-01-02", "duplicate_header"),
        (b"Date,,Amount\n2026-01-01,X,1", "blank_header"),
        (b"Date|Description|Amount\n2026-01-01|X|1", "unsupported_delimiter"),
        (b"Date,Description\n2026-01-01,X,extra", "wide_row"),
    ],
)
def test_rejects_invalid_documents(data: bytes, code: str) -> None:
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(data)
    assert error.value.code == code


def test_ignores_blank_lines_but_limits_data_rows() -> None:
    body = "Date,Description,Amount\n" + "\n".join(
        f"2026-08-01,Example {number},-1.00" for number in range(1001)
    )
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(body.encode())
    assert error.value.code == "too_many_rows"


def test_rejects_more_than_fifty_columns() -> None:
    headers = ",".join(f"Column {number}" for number in range(51))
    values = ",".join("x" for _ in range(51))
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(f"{headers}\n{values}\n".encode())
    assert error.value.code == "too_many_columns"


def test_rejects_a_field_over_two_thousand_characters() -> None:
    with pytest.raises(CsvValidationError) as error:
        parse_csv_bytes(f"Date,Description\n2026-08-01,{'x' * 2001}\n".encode())
    assert error.value.code == "field_too_long"
```

- [ ] **Step 2: Run the focused test and see it fail**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_csv_parser.py -v
```

Expected: collection fails because `app.imports.parser` is missing.

- [ ] **Step 3: Implement the bounded standard-library parser**

In `app/imports/parser.py`, define constants and exception:

```python
MAX_COLUMNS = 50
MAX_ROWS = 1_000
MAX_FIELD_CHARS = 2_000
ALLOWED_DELIMITERS = (",", ";", "\t")


class CsvValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_csv_bytes(data: bytes) -> CsvDocument:
    """Decode UTF-8-SIG and parse a bounded, rectangular CSV document."""
```

Decode with `utf-8-sig`, reject NUL before `csv.Sniffer`, sniff only the three
allowed delimiters, then use `csv.reader(text_io, dialect=dialect, strict=True)`.
Trim headers, keep
field text unmodified for later normalization, ignore fully blank rows, number
rows using the original one-based CSV line number, and emit the exact safe
error codes asserted above. Reject short rows as `short_row` as well as wide
rows.

- [ ] **Step 4: Run parser, mapping, and lint checks; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_csv_parser.py tests\test_import_mapping.py -v
& .\.venv\Scripts\ruff.exe check app\imports tests\test_csv_parser.py
git add app/imports/parser.py tests/test_csv_parser.py
git commit -m "feat: parse bounded CSV statements"
```

Expected: all focused tests pass.

---

### Task 3: Normalize dates, descriptions, and exact cents

**Files:**
- Create: `app/imports/normalization.py`
- Create: `tests/test_import_normalization.py`

**Interfaces:**
- Consumes: `CsvSourceRow`, `ColumnMapping`, and `NormalizedTransaction`.
- Produces: `RowValidationError(row_number, field_errors)`,
  `normalize_source_row(row, mapping) -> NormalizedTransaction`, and
  `normalize_review_edit(row_number, date_value, description_value,
  amount_value, date_format) -> NormalizedTransaction`.

- [ ] **Step 1: Write failing normalization tests**

Create `tests/test_import_normalization.py`:

```python
from datetime import date

import pytest

from app.imports.normalization import (
    RowValidationError,
    normalize_review_edit,
    normalize_source_row,
)
from app.imports.types import ColumnMapping, CsvSourceRow


def single_mapping(**changes: object) -> ColumnMapping:
    values = {
        "date_column": "Date",
        "description_column": "Description",
        "amount_mode": "single",
        "amount_column": "Amount",
        "debit_column": None,
        "credit_column": None,
        "date_format": "mdy",
        "amount_sign": "as_is",
    }
    values.update(changes)
    return ColumnMapping(**values)


def test_normalizes_accounting_money_and_unicode_description() -> None:
    row = CsvSourceRow(
        2,
        {"Date": "08/01/2026", "Description": "  Café   Market  ", "Amount": "($1,234.50)"},
    )
    transaction = normalize_source_row(row, single_mapping())
    assert transaction.transaction_date == date(2026, 8, 1)
    assert transaction.description == "Café Market"
    assert transaction.normalized_merchant == "CAFÉ MARKET"
    assert transaction.amount_cents == -123_450


def test_split_debit_is_negative_and_credit_is_positive() -> None:
    mapping = ColumnMapping("Date", "Description", "split", None, "Debit", "Credit", "iso", "as_is")
    debit = normalize_source_row(
        CsvSourceRow(
            2, {"Date": "2026-08-01", "Description": "Store", "Debit": "4.20", "Credit": ""}
        ),
        mapping,
    )
    credit = normalize_source_row(
        CsvSourceRow(
            3, {"Date": "2026-08-02", "Description": "Refund", "Debit": "", "Credit": "4.20"}
        ),
        mapping,
    )
    assert debit.amount_cents == -420
    assert credit.amount_cents == 420


@pytest.mark.parametrize("amount", ["NaN", "1.001", "0", "$", "1e9"])
def test_invalid_money_has_an_amount_error(amount: str) -> None:
    with pytest.raises(RowValidationError) as error:
        normalize_review_edit(2, "08/01/2026", "Store", amount, "mdy")
    assert "amount" in error.value.field_errors


def test_split_row_rejects_two_nonzero_values() -> None:
    mapping = ColumnMapping("Date", "Description", "split", None, "Debit", "Credit", "dmy", "as_is")
    with pytest.raises(RowValidationError) as error:
        normalize_source_row(
            CsvSourceRow(
                2, {"Date": "01/08/2026", "Description": "Store", "Debit": "1", "Credit": "2"}
            ),
            mapping,
        )
    assert error.value.field_errors["amount"] == "Enter a debit or credit, not both."


def test_description_and_date_errors_are_returned_together() -> None:
    with pytest.raises(RowValidationError) as error:
        normalize_review_edit(9, "02/30/2026", "   ", "1.00", "mdy")
    assert error.value.row_number == 9
    assert set(error.value.field_errors) == {"date", "description"}
```

- [ ] **Step 2: Run and confirm the intended missing-module failure**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_normalization.py -v
```

Expected: collection fails for the missing normalization module.

- [ ] **Step 3: Implement exact normalization**

Use `unicodedata.normalize("NFKC", value)`, `" ".join(value.split())`,
`Decimal`, and `datetime.strptime`. Allow only a leading `$`, grouping commas,
or a single surrounding parentheses pair. Explicitly reject exponent notation,
non-finite values, zero, more than two decimal places, blank descriptions, and
descriptions over 512 characters. Map formats exactly:

```python
DATE_FORMATS = {"iso": "%Y-%m-%d", "mdy": "%m/%d/%Y", "dmy": "%d/%m/%Y"}
```

For source rows, apply `amount_sign == "invert"` only after parsing a single
amount. For review edits, the amount is already a canonical signed value and is
never inverted again. Collect all independent field errors before raising.

- [ ] **Step 4: Run focused tests, format, and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_normalization.py -v
& .\.venv\Scripts\ruff.exe format app\imports tests\test_import_normalization.py
& .\.venv\Scripts\ruff.exe check app\imports tests\test_import_normalization.py
git add app/imports/normalization.py tests/test_import_normalization.py
git commit -m "feat: normalize imported transaction rows"
```

Expected: normalization tests pass with no float arithmetic.

---

### Task 4: Generate stable duplicate fingerprints

**Files:**
- Create: `app/imports/duplicates.py`
- Create: `tests/test_import_duplicates.py`

**Interfaces:**
- Consumes: ordered `NormalizedTransaction` values and the existing
  `Transaction` ORM model.
- Produces:
  `fingerprint_transactions(rows) -> tuple[FingerprintedTransaction, ...]` and
  `find_existing_fingerprints(db, workspace_id, fingerprints) -> set[str]`.

- [ ] **Step 1: Write failing fingerprint tests**

Create `tests/test_import_duplicates.py`:

```python
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Transaction, Workspace
from app.imports.duplicates import (
    find_existing_fingerprints,
    fingerprint_transactions,
)
from app.imports.types import NormalizedTransaction


def row(number: int, merchant: str = "EXAMPLE MARKET") -> NormalizedTransaction:
    return NormalizedTransaction(number, date(2026, 8, 1), merchant.title(), merchant, -1234)


def test_same_rows_produce_stable_versioned_fingerprints() -> None:
    first = fingerprint_transactions((row(2), row(3)))
    second = fingerprint_transactions((row(2), row(3)))
    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert [item.occurrence for item in first] == [1, 2]
    assert first[0].fingerprint != first[1].fingerprint


def test_changed_merchant_changes_the_fingerprint() -> None:
    assert (
        fingerprint_transactions((row(2),))[0].fingerprint
        != fingerprint_transactions((row(2, "OTHER MARKET"),))[0].fingerprint
    )


def test_existing_lookup_is_workspace_scoped(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    fingerprint = fingerprint_transactions((row(2),))[0].fingerprint
    session.add(
        Transaction(
            workspace_id=other_workspace.id,
            date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            description="Example Market",
            normalized_merchant="EXAMPLE MARKET",
            amount_cents=-1234,
            duplicate_fingerprint=fingerprint,
        )
    )
    session.commit()
    assert find_existing_fingerprints(session, workspace.id, {fingerprint}) == set()
    assert find_existing_fingerprints(session, other_workspace.id, {fingerprint}) == {fingerprint}
```

Add an `other_workspace` fixture in `tests/conftest.py` only if PR 3 does not
already provide one. It must have a different owner and no shared membership.

- [ ] **Step 2: Run the test and verify red**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_duplicates.py -v
```

Expected: import fails because `app.imports.duplicates` is missing.

- [ ] **Step 3: Implement versioned occurrence-aware SHA-256**

For each row in input order, count occurrences by
`(transaction_date, amount_cents, normalized_merchant)`. Hash this exact UTF-8
payload:

```python
payload = (
    f"v1\n{item.transaction_date.isoformat()}\n{item.amount_cents}\n"
    f"{item.normalized_merchant}\n{occurrence}"
)
fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Implement the database lookup with explicit
`Transaction.workspace_id == workspace_id` and chunks of at most 500 values so
SQLite's bind-parameter limit is not exceeded. Empty input returns `set()`
without a query.

- [ ] **Step 4: Run focused and database tests; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_duplicates.py tests\test_imports.py -v
& .\.venv\Scripts\ruff.exe check app\imports tests
git add app/imports/duplicates.py tests/test_import_duplicates.py tests/conftest.py
git commit -m "feat: fingerprint duplicate transactions"
```

Expected: duplicate tests and existing unique-constraint tests pass.

---

### Task 5: Store uploads behind a private local adapter

**Files:**
- Modify: `app/core/config.py`
- Create: `app/imports/storage.py`
- Create: `tests/test_upload_storage.py`

**Interfaces:**
- Consumes: a configured root `Path` and a binary stream.
- Produces: `StoredUpload(storage_key, checksum, size_bytes)`,
  `UploadStorageError(code, message)`, and `LocalUploadStore.save/read/delete`.

- [ ] **Step 1: Write storage tests using `tmp_path`**

Create `tests/test_upload_storage.py`:

```python
from io import BytesIO
from pathlib import Path

import pytest

from app.imports.storage import LocalUploadStore, UploadStorageError


def test_save_uses_an_opaque_workspace_key(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path, max_bytes=20)
    source = b"a,b\n1,2\n"
    saved = store.save(42, BytesIO(source))
    assert saved.storage_key.startswith("42/")
    assert saved.storage_key.endswith(".csv")
    assert "statement" not in saved.storage_key
    assert saved.size_bytes == len(source)
    assert store.read(saved.storage_key) == source


def test_oversize_upload_removes_partial_file(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path, max_bytes=5)
    with pytest.raises(UploadStorageError) as error:
        store.save(1, BytesIO(b"123456"))
    assert error.value.code == "file_too_large"
    assert list(tmp_path.rglob("*.csv")) == []


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path)
    saved = store.save(1, BytesIO(b"a,b\n1,2\n"))
    store.delete(saved.storage_key)
    store.delete(saved.storage_key)
    assert not (tmp_path / saved.storage_key).exists()


@pytest.mark.parametrize("key", ["../secret.csv", "/absolute.csv", "1/../../secret.csv"])
def test_read_rejects_paths_outside_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(UploadStorageError) as error:
        LocalUploadStore(tmp_path).read(key)
    assert error.value.code == "invalid_storage_key"
```

- [ ] **Step 2: Run and confirm the missing adapter failure**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_upload_storage.py -v
```

Expected: collection fails for missing `app.imports.storage`.

- [ ] **Step 3: Add settings and implement streaming storage**

Add to `Settings`:

```python
upload_directory: Path = Path("data/uploads")
max_csv_upload_bytes: int = 5 * 1024 * 1024
```

Import `Path` from `pathlib`. Implement:

```python
@dataclass(frozen=True)
class StoredUpload:
    storage_key: str
    checksum: str
    size_bytes: int


class LocalUploadStore:
    """Store and remove opaque workspace files below one configured root."""
```

Add `__init__(root: Path, max_bytes: int = 5 * 1024 * 1024)`,
`save(workspace_id: int, stream: BinaryIO) -> StoredUpload`,
`read(storage_key: str) -> bytes`, and `delete(storage_key: str) -> None`.
Read/write 64 KiB chunks, update `hashlib.sha256`, generate the key with
`uuid.uuid4().hex`, use exclusive file creation, and delete partial output in a
`finally` block unless save completed. Resolve every read/delete target and
verify `target.is_relative_to(root.resolve())` before access. `delete` ignores a
missing generated file but rejects an invalid key.

- [ ] **Step 4: Run storage tests, all config tests, and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_upload_storage.py tests\test_app.py -v
& .\.venv\Scripts\ruff.exe format app\core\config.py app\imports\storage.py tests\test_upload_storage.py
& .\.venv\Scripts\ruff.exe check app\core\config.py app\imports tests\test_upload_storage.py
git add app/core/config.py app/imports/storage.py tests/test_upload_storage.py
git commit -m "feat: store private CSV uploads"
```

Expected: storage and existing app tests pass.

---

### Task 6: Seed built-in categories with a reversible migration

**Files:**
- Create: `migrations/versions/0006_builtin_categories.py`
- Create: `tests/test_builtin_categories.py`

**Interfaces:**
- Consumes: the `categories` table from revision 0002 and current head 0005.
- Produces: Alembic head `0006_builtin_categories` and the exact global category
  name/kind pairs from the design.

- [ ] **Step 1: Write the failing category expectation**

Create `tests/test_builtin_categories.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Category


EXPECTED_BUILTINS = {
    ("Uncategorized", "expense"),
    ("Groceries", "expense"),
    ("Dining", "expense"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Health", "expense"),
    ("Income", "income"),
    ("Transfers", "transfer"),
}


def test_expected_builtin_category_contract() -> None:
    assert len(EXPECTED_BUILTINS) == 11


def test_global_categories_are_visible_to_a_workspace(session: Session) -> None:
    session.add_all(
        [Category(workspace_id=None, name=name, kind=kind) for name, kind in EXPECTED_BUILTINS]
    )
    session.commit()
    actual = set(
        session.execute(
            select(Category.name, Category.kind).where(Category.workspace_id.is_(None))
        ).all()
    )
    assert actual == EXPECTED_BUILTINS
```

This ORM test documents visibility. The red migration proof is the next step.

- [ ] **Step 2: Prove a fresh migrated database lacks the seed**

Run:

```powershell
$dbPath = Join-Path ([System.IO.Path]::GetTempPath()) ('wimm-pr4-red-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($dbPath -replace '\\','/')
try {
    & .\.venv\Scripts\alembic.exe upgrade head
    @'
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    print(connection.execute(text("select count(*) from categories where workspace_id is null")).scalar_one())
'@ | & .\.venv\Scripts\python.exe -
} finally {
    Remove-Item -LiteralPath $dbPath -ErrorAction SilentlyContinue
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
```

Expected before revision 0006 exists: printed count is `0`.

- [ ] **Step 3: Add data-only revision 0006**

Set:

```python
revision = "0006_builtin_categories"
down_revision = "0005_accounts_balances"
```

Use `sa.table`/`sa.column` and `op.get_bind()`. For each exact pair, select a
global match and insert only when absent. Downgrade deletes only rows where
`workspace_id IS NULL` and both name and kind match an expected pair. Do not
assume stable primary-key IDs.

- [ ] **Step 4: Verify upgrade, downgrade, and re-upgrade data**

Run a fresh-file cycle:

```powershell
$dbPath = Join-Path ([System.IO.Path]::GetTempPath()) ('wimm-pr4-green-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($dbPath -replace '\\','/')
try {
    & .\.venv\Scripts\alembic.exe upgrade head
    & .\.venv\Scripts\alembic.exe downgrade 0005_accounts_balances
    & .\.venv\Scripts\alembic.exe upgrade head
} finally {
    Remove-Item -LiteralPath $dbPath -ErrorAction SilentlyContinue
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
```

Expected: all commands exit zero and final head is 0006. Query the final file
before deletion in the implementation run and assert exactly 11 global rows.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_builtin_categories.py tests\test_imports.py -v
& .\.venv\Scripts\ruff.exe check migrations\versions\0006_builtin_categories.py tests\test_builtin_categories.py
git add migrations/versions/0006_builtin_categories.py tests/test_builtin_categories.py
git commit -m "feat: seed built-in transaction categories"
```

Expected: ORM and migration checks pass.

---

### Task 7: Create and scope import jobs safely

**Files:**
- Create: `app/imports/service.py`
- Create: `tests/test_import_service.py`

**Interfaces:**
- Consumes: `UploadedFile`, `ImportJob`, `Workspace`, `LocalUploadStore`,
  `parse_csv_bytes`, and a SQLAlchemy `Session`.
- Produces: `ImportStateError`, `UploadResult`,
  `get_workspace_import(db, workspace_id, import_id)`,
  `create_csv_import(db, store, workspace, upload, retention_choice)`, and
  `cancel_import(db, store, job)`.

- [ ] **Step 1: Write failing service tests for scoping and exact files**

Add tests that create synthetic `BytesIO` uploads:

```python
CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example,-1.00\n"


def test_create_job_links_private_file_and_checksum(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    result = create_csv_import(
        session,
        LocalUploadStore(tmp_path),
        workspace,
        BytesIO(b"Date,Description,Amount\n08/01/2026,Example,-1.00\n"),
        "delete_after_import",
    )
    assert result.kind == "created"
    assert result.job.status == "awaiting_mapping"
    assert result.job.workspace_id == workspace.id
    assert result.job.uploaded_file.retention_choice == "delete_after_import"
    assert result.job.source_checksum == result.job.uploaded_file.checksum


def test_committed_exact_reupload_creates_no_second_job(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    first = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")
    first.job.status = "committed"
    session.commit()
    second = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")
    assert second.kind == "already_committed"
    assert second.job.id == first.job.id
    assert session.query(ImportJob).count() == 1
    assert len(list(tmp_path.rglob("*.csv"))) == 1


def test_active_exact_reupload_resumes_existing_job(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    store = LocalUploadStore(tmp_path)
    first = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")
    second = create_csv_import(session, store, workspace, BytesIO(CSV_BYTES), "delete_after_import")
    assert second.kind == "resume"
    assert second.job.id == first.job.id


def test_import_lookup_hides_other_workspace(
    session: Session, workspace: Workspace, other_workspace: Workspace
) -> None:
    job = ImportJob(workspace_id=other_workspace.id, status="reviewing")
    session.add(job)
    session.commit()
    assert get_workspace_import(session, workspace.id, job.id) is None


def test_invalid_csv_deletes_source_and_creates_no_records(
    session: Session, workspace: Workspace, tmp_path: Path
) -> None:
    with pytest.raises(CsvValidationError):
        create_csv_import(
            session,
            LocalUploadStore(tmp_path),
            workspace,
            BytesIO(b"not|supported"),
            "delete_after_import",
        )
    assert session.query(ImportJob).count() == 0
    assert session.query(UploadedFile).count() == 0
    assert list(tmp_path.rglob("*.csv")) == []
```

- [ ] **Step 2: Run and confirm the new service symbols are missing**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py -v
```

Expected: import fails for missing service symbols.

- [ ] **Step 3: Implement upload creation with compensating cleanup**

Define:

```python
UploadResultKind = Literal["created", "resume", "already_committed"]


@dataclass(frozen=True)
class UploadResult:
    kind: UploadResultKind
    job: ImportJob


def create_csv_import(
    db: Session,
    store: LocalUploadStore,
    workspace: Workspace,
    upload: BinaryIO,
    retention_choice: str,
) -> UploadResult:
```

Validate retention against `{"delete_after_import", "retain"}`. Save then
parse before adding ORM records. Query exact checksum with an explicit
workspace predicate. Treat `committed` and `committed_cleanup_failed` as
already committed; treat `awaiting_mapping` and `reviewing` as resumable. Delete
the newly written duplicate copy. For a new file, add `UploadedFile` and
`ImportJob`, flush/commit, and return it. On parse or database failure, roll
back and delete the newly written file before re-raising.

- [ ] **Step 4: Implement cancellation states and tests**

Add tests proving cancellation deletes the source even for `retain`, marks
`UploadedFile.deleted`, sets `canceled`, rejects cancellation after commit, and
sets `canceled_cleanup_failed` plus
`validation_errors={"cleanup": "delete_failed"}` when a fake store raises.
Implement `cancel_import` with only those transitions.

- [ ] **Step 5: Run service/storage/parser tests and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py tests\test_upload_storage.py tests\test_csv_parser.py -v
& .\.venv\Scripts\ruff.exe format app\imports tests\test_import_service.py
& .\.venv\Scripts\ruff.exe check app\imports tests\test_import_service.py
git add app/imports/service.py tests/test_import_service.py
git commit -m "feat: create private CSV import jobs"
```

Expected: job, duplicate-file, cleanup, and scoping tests pass.

---

### Task 8: Build review rows and persist a validated mapping

**Files:**
- Modify: `app/imports/types.py`
- Modify: `app/imports/service.py`
- Modify: `tests/test_import_service.py`

**Interfaces:**
- Consumes: a workspace-scoped job, its private source, `ColumnMapping`, row
  normalizer, fingerprint service, and SQLAlchemy session.
- Produces: `ReviewRow`, `ImportReview`,
  `save_mapping(db, store, job, form) -> ColumnMapping`, and
  `build_review(db, store, job) -> ImportReview`.

- [ ] **Step 1: Write failing mapping-state tests**

Add concrete tests to `tests/test_import_service.py` that:

1. create a job from bytes with `Date,Description,Amount`;
2. call `save_mapping` with an `mdy` single-amount mapping;
3. assert `job.status == "reviewing"` and `job.column_mapping` equals the exact
   eight-key JSON shape;
4. call it again while reviewing with `amount_sign="invert"` and assert the
   mapping changes;
5. set the job to `committed` and assert `ImportStateError` with code
   `mapping_not_editable`.

Use this exact expected JSON:

```python
{
    "date_column": "Date",
    "description_column": "Description",
    "amount_mode": "single",
    "amount_column": "Amount",
    "debit_column": None,
    "credit_column": None,
    "date_format": "mdy",
    "amount_sign": "as_is",
}
```

- [ ] **Step 2: Add failing review-output tests**

Use a source with two valid rows, one invalid amount, and one row matching an
existing transaction. Assert the returned review has:

```python
assert review.total_rows == 4
assert review.valid_rows == 3
assert review.invalid_rows == 1
assert review.duplicate_rows == 1
assert review.rows[0].included is True
assert review.rows[2].field_errors == {"amount": "Enter a valid amount."}
assert review.rows[3].duplicate is True
assert review.rows[3].included is False
```

Also delete the source before `build_review` and assert a safe
`ImportStateError(code="source_missing")` rather than use stale mapping data.

- [ ] **Step 3: Run service tests and confirm new symbols fail**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py -v
```

Expected: failures name `save_mapping`, `build_review`, `ReviewRow`, or
`ImportReview`.

- [ ] **Step 4: Add exact review types**

In `app/imports/types.py`:

```python
@dataclass(frozen=True)
class ReviewRow:
    row_number: int
    date_value: str
    description_value: str
    amount_value: str
    normalized: NormalizedTransaction | None
    fingerprint: str | None
    duplicate: bool
    included: bool
    field_errors: dict[str, str]


@dataclass(frozen=True)
class ImportReview:
    rows: tuple[ReviewRow, ...]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
```

Canonical `date_value` uses ISO format and canonical `amount_value` uses a sign
and two decimals (for example `-12.34`) after successful normalization. Invalid
rows keep the mapped raw strings so the form can correct them.

- [ ] **Step 5: Implement mapping persistence and deterministic review**

`save_mapping` must accept only `awaiting_mapping`/`reviewing`, read and parse
the source, validate against its exact headers, save `mapping.to_json()`, clear
old validation errors, set `reviewing`, and commit.

`build_review` must require `reviewing`, re-read/reparse the file, rebuild
`mapping_from_json`, normalize every row while collecting field errors,
fingerprint valid rows in source order, make one workspace-scoped duplicate
lookup, and return immutable review rows. It performs no database writes.

- [ ] **Step 6: Run focused and full import tests; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py tests\test_import_normalization.py tests\test_import_duplicates.py -v
& .\.venv\Scripts\ruff.exe check app\imports tests\test_import_service.py
git add app/imports/types.py app/imports/service.py tests/test_import_service.py
git commit -m "feat: prepare CSV imports for review"
```

Expected: review counts, errors, duplicate flags, and states pass.

---

### Task 9: Commit reviewed edits atomically and recover cleanup failures

**Files:**
- Modify: `app/imports/service.py`
- Modify: `tests/test_import_service.py`

**Interfaces:**
- Consumes: `ImportReview`, posted `RowEdit` values, built-in
  `Uncategorized`, `Transaction`, and the private source.
- Produces: `RowEdit`, `CommitResult`,
  `commit_import(db, store, job, edits) -> CommitResult`, and
  `retry_cleanup(db, store, job) -> ImportJob`.

- [ ] **Step 1: Add exact edit/result types and failing happy-path test**

Define the intended types in the test import before implementation:

```python
@dataclass(frozen=True)
class RowEdit:
    row_number: int
    include: bool
    date_value: str
    description_value: str
    amount_value: str


@dataclass(frozen=True)
class CommitResult:
    job: ImportJob
    inserted_count: int
    duplicate_count: int
    excluded_count: int
    cleanup_failed: bool
```

The happy-path test must create/map a two-row source, edit the first
description, exclude the second, call `commit_import`, and assert:

```python
assert result.inserted_count == 1
assert result.excluded_count == 1
assert result.duplicate_count == 0
assert result.job.status == "committed"
transactions = session.scalars(select(Transaction)).all()
assert len(transactions) == 1
assert transactions[0].description == "Corrected Market"
assert transactions[0].amount_cents == -1234
assert transactions[0].date.date() == date(2026, 8, 1)
assert transactions[0].date.time() == time.min
assert transactions[0].category.name == "Uncategorized"
assert transactions[0].categorization_source == "uncategorized"
assert transactions[0].duplicate_fingerprint is not None
assert result.job.uploaded_file.deleted is True
```

- [ ] **Step 2: Add failing safety and idempotency tests**

Add separate tests proving:

- no transaction exists before `commit_import`;
- a missing/extra row number returns `ImportStateError("review_rows_changed")`;
- an included invalid edit returns `ReviewValidationError` and inserts zero;
- all rows excluded/duplicate returns `ReviewValidationError("no_rows_selected")`;
- an existing fingerprint is counted and skipped;
- a second call after commit returns inserted count 0 and creates no rows;
- a forced SQLAlchemy flush failure rolls back every candidate and leaves job
  `reviewing` plus the source file present;
- the existing unique constraint converts a concurrent winner into
  `ImportStateError("duplicate_commit_conflict")` with zero partial rows;
- retained sources remain on disk and `UploadedFile.deleted` stays false;
- a fake delete failure commits transactions, sets
  `committed_cleanup_failed`, and stores only `{"cleanup": "delete_failed"}`;
- `retry_cleanup` deletes the source, sets `deleted = True`, clears the safe
  cleanup error, and changes status to `committed`;
- retry cleanup works the same way from `canceled_cleanup_failed` to `canceled`.

- [ ] **Step 3: Run service tests and confirm the new API is red**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py -v
```

Expected: failures are limited to missing commit/edit/cleanup behavior.

- [ ] **Step 4: Implement posted-review validation**

Before inserting, call `build_review` to prove the source still exists and get
the exact source row-number set. Require posted row numbers to match it exactly
with no duplicates. Normalize every included edit using the mapping's date
format; fingerprint included normalized rows in posted source order; query
existing fingerprints again. Do not trust duplicate booleans or fingerprints
from hidden form fields.

Load `Uncategorized` with:

```python
select(Category).where(
    Category.workspace_id.is_(None),
    Category.name == "Uncategorized",
    Category.kind == "expense",
)
```

Treat absence or more than one match as an explicit configuration error; do
not create a category opportunistically in request code.

- [ ] **Step 5: Implement the atomic SQLAlchemy unit of work**

For each non-duplicate included row, add:

```python
Transaction(
    workspace_id=job.workspace_id,
    date=datetime.combine(item.transaction_date, time.min, tzinfo=timezone.utc),
    description=item.description,
    normalized_merchant=item.normalized_merchant,
    amount_cents=item.amount_cents,
    category_id=uncategorized.id,
    categorization_source="uncategorized",
    duplicate_fingerprint=fingerprinted.fingerprint,
    import_job_id=job.id,
)
```

Set `job.status = "committed"` in the same transaction and call `db.commit()`
once. Catch `IntegrityError`, roll back, and raise the safe conflict. Never
delete the raw source before this commit succeeds.

- [ ] **Step 6: Implement post-commit retention cleanup and retry**

For `retain`, return immediately. For `delete_after_import`, call
`store.delete` after the transaction. On success, set `uploaded_file.deleted =
True` and commit that metadata. On failure, set
`committed_cleanup_failed`/safe validation error and commit that truthful state.
`retry_cleanup` accepts only the two cleanup-failed states and never touches
transactions.

- [ ] **Step 7: Run service, database, and full tests; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_service.py tests\test_imports.py -v
& .\.venv\Scripts\pytest.exe
& .\.venv\Scripts\ruff.exe format app\imports tests\test_import_service.py
& .\.venv\Scripts\ruff.exe check app\imports tests
git add app/imports/service.py tests/test_import_service.py
git commit -m "feat: commit reviewed CSV transactions"
```

Expected: all tests pass; commit remains atomic under every failure test.

---

### Task 10: Add authenticated upload, mapping, review, and lifecycle routes

**Files:**
- Create: `app/imports/routes.py`
- Create: `app/templates/imports/upload.html`
- Create: `app/templates/imports/mapping.html`
- Create: `app/templates/imports/review.html`
- Create: `app/templates/imports/result.html`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Create: `tests/test_import_routes.py`

**Interfaces:**
- Consumes: PR 3 `require_current_user`, `require_workspace`, `require_csrf`,
  `request.state.csrf_token`, `get_db`, Jinja environment, settings,
  and Tasks 1–9 services.
- Produces: named FastAPI routes `new_import`, `create_import`, `map_import`,
  `save_import_mapping`, `review_import`, `commit_import_route`,
  `cancel_import_route`, and `cleanup_import_route`.

- [ ] **Step 1: Build route test fixtures without live OAuth**

In `tests/test_import_routes.py`, reuse PR 3's fake-Google/session client
pattern. Override `get_db` with the test session and the store dependency with
`LocalUploadStore(tmp_path)`. Keep the real `require_workspace` dependency in
all authorization tests so the membership query is exercised. Use PR 3's
genuine CSRF middleware/cookie/form token rather than disabling CSRF globally.

Add a helper that posts this synthetic file:

```python
files = {
    "statement": (
        "synthetic.csv",
        b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n",
        "text/csv",
    )
}
data = {"retention_choice": "delete_after_import", "csrf_token": csrf_token}
```

- [ ] **Step 2: Write failing access and CSRF route tests**

Add tests for:

- unauthenticated `GET /workspaces/{id}/imports/new` follows PR 3's exact
  sign-in response;
- a nonmember gets 404 for upload, mapping, review, commit, cancel, and cleanup;
- POST without/with invalid CSRF creates no file, job, or transaction;
- an import ID owned by another workspace returns 404 for every job route.

Assert database counts and `list(tmp_path.rglob("*.csv")) == []` after rejected
upload posts.

- [ ] **Step 3: Write failing upload and mapping route tests**

Assert:

- the new-import page has `.csv`, retention, size, and privacy guidance;
- wrong extension/content type, over-5-MiB, invalid UTF-8, and invalid mapping
  return helpful form errors and no leaked path/checksum;
- a valid upload returns `303` to its workspace-scoped mapping URL;
- exact active re-upload redirects to that job; committed re-upload redirects
  to the transaction list with an `already_imported=1` notice;
- mapping GET shows only headers and the first 10 raw rows;
- valid mapping POST returns `303` to review; invalid mapping stays on mapping
  with field errors and status unchanged.

- [ ] **Step 4: Write failing review/commit/lifecycle route tests**

Assert review renders canonical signed amounts, an editable date/description/
amount for each row, include checkboxes, locked duplicate rows, counts, CSRF,
and no storage key/checksum. Post form fields named exactly:

```text
row_numbers=2
row_2_include=on
row_2_date=2026-08-01
row_2_description=Corrected Market
row_2_amount=-12.34
```

For multiple rows, repeat `row_numbers` in source order. Assert invalid edits
re-render review without transactions. Assert valid commit redirects to the
result page, a second commit does not duplicate, cancel deletes an uncommitted
source, and cleanup retry transitions a fake cleanup failure.

- [ ] **Step 5: Run route tests and confirm route/module failures**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_routes.py -v
```

Expected: failures identify missing router/templates/registration.

- [ ] **Step 6: Implement a thin router and explicit dependencies**

Create `APIRouter(prefix="/workspaces/{workspace_id}")`. Each handler receives
the authorized `Workspace` from PR 3 and a SQLAlchemy session. Every POST invokes PR
3's CSRF dependency before reading/mutating upload data. Build
`LocalUploadStore(settings.upload_directory, settings.max_csv_upload_bytes)`
through an overrideable dependency.

Validate submitted filename/content type in the route, but delegate byte size,
CSV validity, mapping, state, duplicates, and commits to their services. Parse
review edits from `await request.form()` by the exact repeated `row_numbers`
protocol; reject missing, duplicate, non-integer, or unexpected row field names
as `review_rows_changed`.

- [ ] **Step 7: Create beginner-readable Jinja forms**

All templates extend PR 3's base and display the active workspace. Include the
exact signed CSRF field PR 3 expects. `upload.html` defaults to delete and says
the source must remain private until review. `mapping.html` explains single vs
split amounts and all three date formats. `review.html` explains negative/
positive signs and duplicate locks. `result.html` reports inserted, duplicate,
and excluded counts plus any cleanup warning/retry action.

Never render `storage_path`, `checksum`, raw source beyond the ten-row mapping
preview, session content, or foreign-workspace information.

- [ ] **Step 8: Register routes and navigation**

In `app/main.py` import/include the router after app construction. Add links in
the signed-in workspace navigation to named routes, using the authorized active
workspace ID. Do not hardcode paths in templates where `url_for` can use route
names.

- [ ] **Step 9: Run route, service, and full tests; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_import_routes.py tests\test_import_service.py -v
& .\.venv\Scripts\pytest.exe
& .\.venv\Scripts\ruff.exe format app tests\test_import_routes.py
& .\.venv\Scripts\ruff.exe check app tests
git add app/main.py app/imports/routes.py app/templates/imports app/templates/base.html tests/test_import_routes.py
git commit -m "feat: add private CSV review flow"
```

Expected: route/privacy tests and the full suite pass.

---

### Task 11: Query transactions through strict workspace filters

**Files:**
- Create: `app/transactions/__init__.py`
- Create: `app/transactions/queries.py`
- Create: `tests/test_transaction_queries.py`

**Interfaces:**
- Consumes: `Transaction`, `Category`, an authorized workspace ID, and raw query
  parameters.
- Produces: `TransactionFilters`, `TransactionPage`, `FilterValidationError`,
  `parse_filters(params) -> TransactionFilters`, and
  `list_transactions(db, workspace_id, filters) -> TransactionPage`.

- [ ] **Step 1: Write failing filter-parsing tests**

Create tests asserting:

```python
def test_parse_filters_sets_bounded_defaults() -> None:
    filters = parse_filters({})
    assert filters.start_date is None
    assert filters.end_date is None
    assert filters.category_id is None
    assert filters.direction == "all"
    assert filters.query == ""
    assert filters.page == 1
    assert filters.page_size == 50


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"start_date": "08/01/2026"}, "start_date"),
        ({"start_date": "2026-08-02", "end_date": "2026-08-01"}, "end_date"),
        ({"direction": "transfer"}, "direction"),
        ({"page": "0"}, "page"),
        ({"page": "abc"}, "page"),
        ({"category_id": "abc"}, "category_id"),
        ({"q": "x" * 101}, "q"),
    ],
)
def test_invalid_filter_is_rejected(params: dict[str, str], field: str) -> None:
    with pytest.raises(FilterValidationError) as error:
        parse_filters(params)
    assert field in error.value.field_errors
```

- [ ] **Step 2: Write failing workspace/query behavior tests**

Seed at least six transactions across two workspaces, dates, signs, categories,
and merchants. Assert independently that:

- only the requested workspace appears;
- start/end dates are inclusive;
- `expense` returns negative and `income` positive amounts;
- a global category and same-workspace category are accepted;
- another workspace's category raises `FilterValidationError` without leaking
  its name;
- `q` matches description or normalized merchant case-insensitively and treats
  `%`/`_` as literal characters;
- ordering is date descending then ID descending;
- page 2 uses offset 50 and total count/page count are correct.

- [ ] **Step 3: Run and confirm the missing query package failure**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_transaction_queries.py -v
```

Expected: collection fails for missing `app.transactions.queries`.

- [ ] **Step 4: Implement immutable filter/page types**

```python
@dataclass(frozen=True)
class TransactionFilters:
    start_date: date | None = None
    end_date: date | None = None
    category_id: int | None = None
    direction: Literal["all", "expense", "income"] = "all"
    query: str = ""
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True)
class TransactionPage:
    items: tuple[Transaction, ...]
    total_items: int
    page: int
    page_size: int
    total_pages: int
```

`parse_filters` accepts only ISO query dates, strips search whitespace, fixes
page size at 50, and aggregates independent validation errors.

- [ ] **Step 5: Implement the scoped SQLAlchemy query**

Start both item and count statements with
`Transaction.workspace_id == workspace_id`. Convert inclusive end date to the
next midnight UTC and use `< next_day`; use `>= start midnight UTC`. Validate
category with:

```python
or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id)
```

Use `func.lower(Transaction.description).contains(query.lower(), autoescape=True)`
OR the same predicate for `normalized_merchant`. Apply deterministic order,
limit 50, and offset `(page - 1) * 50`. Calculate total pages as zero for zero
items, otherwise ceiling division.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_transaction_queries.py -v
& .\.venv\Scripts\ruff.exe format app\transactions tests\test_transaction_queries.py
& .\.venv\Scripts\ruff.exe check app\transactions tests\test_transaction_queries.py
git add app/transactions tests/test_transaction_queries.py
git commit -m "feat: filter workspace transactions"
```

Expected: every filter and privacy test passes.

---

### Task 12: Render the authenticated transaction list

**Files:**
- Create: `app/transactions/routes.py`
- Create: `app/templates/transactions/list.html`
- Modify: `app/main.py`
- Modify: `app/templates/base.html`
- Create: `tests/test_transaction_routes.py`

**Interfaces:**
- Consumes: PR 3 workspace dependency, `parse_filters`, `list_transactions`,
  accessible global/workspace categories, and Jinja templates.
- Produces: named route `transaction_list` at
  `GET /workspaces/{workspace_id}/transactions`.

- [ ] **Step 1: Write failing access/list tests**

Use PR 3's authenticated test client pattern. Assert unauthenticated behavior,
404 for a nonmember, and that the authorized page contains only its workspace's
synthetic descriptions. Insert a secret-looking description in another
workspace and assert it is absent from both successful and validation-error
responses.

- [ ] **Step 2: Write failing filter/form tests**

Request:

```text
/workspaces/1/transactions?start_date=2026-08-01&end_date=2026-08-31&category_id=2&direction=expense&q=market&page=1
```

Assert form values remain selected, only matching rows render, signed amounts
display as `$12.34` with a clear “money out” label/class, and pagination links
preserve all filters. Assert invalid filters return 422 with the specific field
error and no transaction query outside the workspace.

- [ ] **Step 3: Run and confirm route/template failures**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_transaction_routes.py -v
```

Expected: failures identify the missing route/template.

- [ ] **Step 4: Implement the thin list route**

Create an `APIRouter(prefix="/workspaces/{workspace_id}")`. Receive the PR 3
authorized `Workspace`, parse `request.query_params`, and render status 422 when
`FilterValidationError` occurs. Query category choices using:

```python
select(Category)
.where(or_(Category.workspace_id.is_(None), Category.workspace_id == workspace.id))
.order_by(Category.kind, Category.name)
```

Do not accept workspace ID from a query/form field and do not query categories
before workspace authorization succeeds.

- [ ] **Step 5: Build the accessible server-rendered template**

Add labeled controls for start, end, category, direction, and search plus
Apply/Clear actions. Render date, description, category, and signed amount in a
semantic table. Explain negative as money out and positive as money in. Provide
an empty-state link to `new_import`. Add previous/next links only when valid and
use `urlencode` or Jinja-safe query construction so user text is escaped.

- [ ] **Step 6: Register router/navigation and commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_transaction_routes.py tests\test_transaction_queries.py -v
& .\.venv\Scripts\pytest.exe
& .\.venv\Scripts\ruff.exe format app tests\test_transaction_routes.py
& .\.venv\Scripts\ruff.exe check app tests
git add app/main.py app/transactions/routes.py app/templates/transactions app/templates/base.html tests/test_transaction_routes.py
git commit -m "feat: browse workspace transactions"
```

Expected: transaction routes and the full suite pass.

---

### Task 13: Prove the complete review-before-commit acceptance flow

**Files:**
- Create: `tests/fixtures/statements/synthetic_checking.csv`
- Create: `tests/test_csv_import_flow.py`

**Interfaces:**
- Consumes: the completed authenticated routes, PR 3 CSRF/session test helpers,
  local test storage, and the SQLite test session.
- Produces: one end-to-end HTTP acceptance test covering upload through filtered
  list and safe re-upload.

- [ ] **Step 1: Add the synthetic fixture**

Create `tests/fixtures/statements/synthetic_checking.csv` with exactly:

```csv
Posted,Details,Debit,Credit
08/01/2026,EXAMPLE MARKET 1001,12.34,
08/02/2026,EXAMPLE PAYROLL,,2500.00
08/03/2026,EXAMPLE COFFEE,4.50,
```

These names are fictional and contain no personal data.

- [ ] **Step 2: Write the failing acceptance test**

The test must perform real HTTP requests against the ASGI app with dependency
overrides and PR 3's test sign-in/CSRF mechanism:

1. GET the upload form and extract/use its CSRF token.
2. POST the fixture with `delete_after_import`; follow the 303 to mapping.
3. Assert the database has one job, zero transactions, and one private source.
4. POST split mapping: Posted, Details, Debit, Credit, `mdy`.
5. GET review and assert three rows with normalized signed amounts.
6. POST commit after changing row 2 description to `Example Grocery` and
   unchecking row 4 (`EXAMPLE COFFEE`).
7. Assert two transactions, committed job, and deleted raw file.
8. GET the list and assert grocery/payroll appear but coffee does not.
9. GET with `direction=expense&q=grocery` and assert only the corrected expense.
10. Re-upload the same bytes and assert no new job/transaction/file.
11. Sign in as the unrelated workspace user and assert 404 for the original
    import and transaction URLs.

- [ ] **Step 3: Run and confirm any integration gaps**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_csv_import_flow.py -v
```

Expected before final glue corrections: failures point to concrete route,
template form-name, CSRF, or redirect mismatches rather than missing core
parsing behavior.

- [ ] **Step 4: Apply only the minimal integration corrections**

Align route names, form field names, redirect targets, and test dependency
overrides. Do not weaken CSRF, workspace predicates, file cleanup, or duplicate
checks to make the acceptance test pass.

- [ ] **Step 5: Run acceptance and all privacy-focused tests; commit**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_csv_import_flow.py tests\test_import_routes.py tests\test_transaction_routes.py -v
& .\.venv\Scripts\pytest.exe
git add tests/fixtures/statements/synthetic_checking.csv tests/test_csv_import_flow.py app
git commit -m "test: cover reviewed CSV import flow"
```

Expected: acceptance flow and full suite pass.

---

### Task 14: Document the feature and perform release-quality verification

**Files:**
- Modify: `README.md`
- Modify: `docs/where-is-my-money-pr-breakdown.md`
- Review: every PR 4 file and commit

**Interfaces:**
- Consumes: the complete PR 4 implementation.
- Produces: beginner-facing run/use/test instructions and a verified branch
  ready for a production pull request.

- [ ] **Step 1: Update beginner-facing documentation**

In README, explain in plain language:

- sign in and choose a workspace using PR 3;
- upload a synthetic/personal UTF-8 CSV no larger than 5 MiB;
- choose single amount or debit/credit and the explicit date format;
- review/correct/exclude rows before commit;
- raw files delete after successful commit unless retain is selected;
- negative values mean money out, positive mean money in;
- filter the transaction page;
- retained files have no download page and live under private local data;
- tests use only the included synthetic fixture.

Mark PR 4 complete in the breakdown only after verification, and state that PR
5 adds categorization rules/manual recategorization.

- [ ] **Step 2: Run all Python quality gates fresh**

Run:

```powershell
& .\.venv\Scripts\ruff.exe format --check .
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\pytest.exe
```

Expected: formatting, lint, and every test pass. Record the exact test count for
the PR body.

- [ ] **Step 3: Verify the full migration cycle on a fresh SQLite file**

Run:

```powershell
$dbPath = Join-Path ([System.IO.Path]::GetTempPath()) ('wimm-pr4-final-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($dbPath -replace '\\','/')
try {
    & .\.venv\Scripts\alembic.exe upgrade head
    & .\.venv\Scripts\alembic.exe current
    & .\.venv\Scripts\alembic.exe downgrade 0005_accounts_balances
    & .\.venv\Scripts\alembic.exe upgrade head
    @'
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    count = connection.execute(text("select count(*) from categories where workspace_id is null")).scalar_one()
    assert count == 11, count
    print(f"builtin categories: {count}")
'@ | & .\.venv\Scripts\python.exe -
} finally {
    Remove-Item -LiteralPath $dbPath -ErrorAction SilentlyContinue
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}
```

Expected: head is 0006 before downgrade, downgrade/re-upgrade succeeds, and the
final query prints `builtin categories: 11`.

- [ ] **Step 4: Inspect privacy, scope, and repository diff**

Run:

```powershell
git diff --check
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff main...HEAD
rg -n "storage_path|source_checksum|checksum" app/templates
rg -n "workspace_id" app/imports app/transactions
```

Expected: no whitespace errors; templates contain no raw path/checksum fields;
every database-facing feature contains workspace scoping; diff contains only
PR 4 code/tests/templates/migration/docs; no real statements or secrets exist.

- [ ] **Step 5: Commit documentation**

Run:

```powershell
git add README.md docs/where-is-my-money-pr-breakdown.md
git commit -m "docs: explain private CSV imports"
```

- [ ] **Step 6: Review against the design before publishing**

Check each design section against a test/task: PR 3 contract, file validation,
retention and cleanup retry, mapping JSON, normalization, duplicate layers,
review-before-commit, built-ins, list filters, 404 isolation, CSRF, and exact
re-upload. Fix any uncovered requirement test-first and rerun Steps 2–4.

- [ ] **Step 7: Push and open the production PR only now**

Run:

```powershell
git push -u origin codex/pr-4-csv-import-transactions
```

Create a ready PR targeting `main` titled
`PR 4 — private CSV imports and transactions`. The body must list the
review/commit boundary, duplicate behavior, default deletion/optional retention,
workspace/CSRF isolation, built-in categories, list filters, exact test count,
Ruff results, and migration upgrade/downgrade/re-upgrade evidence.

## Blocked/unblocked handoff summary

No production code is authorized by this planning task. After PR 3 merges, the
execution owner starts at Task 0. Tasks 1–9 are mostly pure/service work but must
still be implemented on a branch based on merged PR 3 so their fixtures and
imports use the real application. Tasks 10–14 are strictly blocked on PR 3's
route authorization, sessions, CSRF, base template, and test client behavior.

The execution sequence is intentionally linear at the integration boundaries:

```text
PR3 contract audit
  -> mapping/parser/normalization/fingerprint/storage
  -> built-in migration
  -> import state/review/atomic commit
  -> authorized routes/templates
  -> transaction query/list
  -> acceptance/security verification
  -> production PR
```

## Plan self-review result

- Spec coverage: every requirement in the design maps to Tasks 0–14.
- Placeholders: no task defers error handling, validation, tests, or interface
  names to an unspecified later step.
- Type consistency: mapping, source row, normalized row, fingerprinted row,
  review row, row edit, commit result, filter, and page types have one producer
  and named consumers.
- Scope: PR 3 behavior is consumed, not recreated; PR 5 categorization and PR
  8b accounts remain outside; no production work begins before merged PR 3.
