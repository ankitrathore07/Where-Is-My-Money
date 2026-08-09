# PR 4 — Private CSV Imports and Transaction Browsing Design

## Goal

Build the first user-facing financial-data workflow: an authorized workspace
member uploads a synthetic or personal CSV statement, maps its columns, reviews
and corrects normalized rows, and explicitly commits only approved,
non-duplicate transactions. The same workspace then gets a paginated,
filterable transaction list. Raw files stay private and are deleted after a
successful import by default; retaining the source is an explicit choice.

PR 4 must begin from merged PR 3 because every route and query depends on PR
3's authenticated user, authorized workspace, session, and CSRF boundaries.
This document defines that dependency rather than guessing around it. No PR 4
production code should be opened as a pull request until PR 3 is merged and the
contract checklist in this document passes against the actual code.

## Scope and success criteria

PR 4 includes:

- private CSV upload with a 5 MiB and 1,000-data-row limit;
- UTF-8/UTF-8-BOM decoding and comma, semicolon, or tab delimiters;
- explicit column mapping for dates, descriptions, and one signed amount or
  separate debit/credit columns;
- exact integer-cent normalization, editable row review, row exclusion, and
  an explicit commit step;
- exact-file and normalized-transaction duplicate protection scoped to a
  workspace;
- optional raw-source retention, with deletion after successful commit as the
  privacy-preserving default;
- built-in categories, with imported rows initially assigned to
  `Uncategorized`;
- a workspace-scoped transaction list with date, category, direction, and
  description/merchant filters;
- synthetic fixtures, unit tests, route/integration tests, and a complete
  upload-to-list acceptance test.

PR 4 is done when a sample statement cannot create transactions before review,
commits its approved rows exactly once, safely recognizes an exact re-upload,
omits previously imported normalized rows from a partially overlapping file,
honors raw-file retention, and never exposes another workspace's imports,
files, categories, or transactions.

## Deliberate non-goals

- Google OAuth, sessions, workspace selection, membership, invitations, or
  CSRF implementation; PR 4 consumes those from PR 3.
- Automatic merchant/category rules, custom categories, or manual
  recategorization; those belong to PR 5.
- Account creation and mandatory account selection; account UI belongs to PR
  8b. PR 4 leaves `ImportJob.account_id` nullable.
- OFX, QFX, XLS/XLSX, PDF, image, or non-UTF-8 statement support.
- Background jobs, Celery, LangGraph, JavaScript frameworks, cloud object
  storage, or a bank-data provider.
- Raw-file download or preview endpoints. Retention preserves future audit
  evidence but does not make source files web-accessible in PR 4.
- Bulk import above 1,000 rows or files above 5 MiB. These conservative limits
  keep synchronous SQLite and server-rendered forms understandable.

## Alternatives considered

### 1. Reparse the private source during mapping, review, and commit — chosen

Keep the accepted CSV in private storage until the workflow finishes. Pure
functions parse it deterministically for each step. Review edits are posted
only on the final commit form, validated again on the server, and inserted in
one database transaction. This uses the existing `UploadedFile`, `ImportJob`,
and `Transaction` schema and never stores unconfirmed candidate transactions.

The trade-off is repeated parsing and a deliberately small synchronous row
limit. At 1,000 rows and 5 MiB, that cost is acceptable and the lifecycle is
easy to explain.

### 2. Add an `import_rows` staging table

Persist every parsed candidate and edit before commit. This supports large
imports and resumable row-by-row editing, but adds a migration, cleanup rules,
authorization surface, and two transaction representations. It is premature
for the first CSV workflow.

### 3. Store candidate rows as JSON on `ImportJob`

This avoids another table but can put a large opaque document in SQLite and
makes filtering, partial updates, and schema evolution awkward. It also stores
unapproved financial content outside the purpose-built transaction table.

## Architecture and boundaries

PR 4 remains part of the FastAPI modular monolith. Routes are thin adapters;
deterministic Python services do parsing and normalization; SQLAlchemy services
perform workspace-scoped reads and the atomic commit; a local file-store
adapter owns private source files.

```text
authenticated request
        |
        v
PR 3 authorized Workspace + CSRF check
        |
        v
imports/routes.py ----> LocalUploadStore
        |                       |
        v                       v
parser -> mapping -> normalization -> duplicate service
        |                               |
        +------------ review -----------+
                        |
                        v
               one SQLAlchemy commit
                        |
                        v
              transactions/routes.py
```

Files are split by one responsibility:

- `app/imports/parser.py`: byte decoding, dialect/header checks, source rows;
- `app/imports/mapping.py`: mapping shape and header validation;
- `app/imports/normalization.py`: dates, descriptions, merchants, and cents;
- `app/imports/duplicates.py`: stable fingerprints and existing-match lookup;
- `app/imports/storage.py`: opaque private storage keys and lifecycle;
- `app/imports/service.py`: job state transitions and atomic transaction commit;
- `app/imports/routes.py`: authorized/CSRF-protected HTTP workflow;
- `app/transactions/queries.py`: filter parsing and workspace-scoped query;
- `app/transactions/routes.py`: transaction-list HTTP adapter.

No import or transaction service accepts an arbitrary user ID. HTTP code passes
only the `Workspace` already authorized by PR 3, and every database query still
contains an explicit `workspace_id` predicate as defense in depth.

## Required PR 3 contracts

PR 4 consumes the following behavioral interface. Names may be adapted once PR
3 lands, but PR 4 must not duplicate or bypass the behavior.

```python
# app/auth/dependencies.py
def require_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
) -> User:
    """Return the signed-in ORM user or redirect an HTML request home."""


# app/workspaces/dependencies.py
def require_workspace(
    workspace_id: int,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[Session, Depends(get_db)],
) -> Workspace:
    """Return a member workspace; use 404 for absent or unauthorized IDs."""


# app/core/middleware.py
async def require_csrf(request: Request) -> None:
    """Reject a missing/invalid csrf_token field or X-CSRF-Token header."""


# CSRFMiddleware supplies the template token on every request:
request.state.csrf_token: str
```

Required behavior:

- `require_current_user` uses PR 3's signed, HTTP-only session and never trusts a user
  ID from a path, query string, form field, or cookie other than the signed
  session.
- `require_workspace` authorizes only through membership, which covers the
  owner and accepted equal-access members. It returns `404`, not `403`, for a
  workspace the user cannot access, so the route does not confirm that another
  household exists.
- PR 4 routes put `workspace_id` in the URL and derive the `Workspace` only
  through `require_workspace`; active-workspace UI may link to these
  URLs but must not replace route-level authorization.
- Every PR 4 POST depends on `require_csrf` before file, database, or cleanup
  mutation. The hidden field uses `request.state.csrf_token`, which PR 3's
  middleware signs, mirrors in the `wimm_csrf` cookie, and validates.
- PR 3's base template exposes signed-in navigation and workspace-switching
  links without requiring PR 4 to edit session internals.
- Tests can override `require_current_user`, `require_workspace`, and `get_db`
  for isolated units. Cross-workspace route tests keep the real
  `require_workspace` membership query and authenticate synthetic users through
  PR 3's fake-Google/session helper. Production PR 4 tests never perform live
  Google OAuth.

If PR 3 lands with different names, PR 4 first adds a small compatibility
adapter or updates this plan to the actual signatures. It must not copy session
decoding, membership queries, or CSRF verification into the import package.

## Route contract and workflow states

Routes use explicit workspace and import identifiers:

- `GET /workspaces/{workspace_id}/imports/new` — upload form;
- `POST /workspaces/{workspace_id}/imports` — validate/store source and create
  the job;
- `GET /workspaces/{workspace_id}/imports/{import_id}/mapping` — header preview
  and mapping form;
- `POST /workspaces/{workspace_id}/imports/{import_id}/mapping` — save a valid
  mapping and move to review;
- `GET /workspaces/{workspace_id}/imports/{import_id}/review` — normalized,
  editable rows and duplicate warnings;
- `POST /workspaces/{workspace_id}/imports/{import_id}/commit` — validate the
  posted review, insert approved rows atomically, and clean up if requested;
- `POST /workspaces/{workspace_id}/imports/{import_id}/cancel` — cancel and
  delete the uncommitted source regardless of retention choice;
- `POST /workspaces/{workspace_id}/imports/{import_id}/cleanup` — retry a failed
  privacy cleanup after a commit or cancellation;
- `GET /workspaces/{workspace_id}/transactions` — filterable list.

An import job uses these exact status strings:

```text
awaiting_mapping
reviewing
committed
committed_cleanup_failed
canceled
canceled_cleanup_failed
```

Allowed transitions are:

```text
upload -> awaiting_mapping -> reviewing -> committed
              |              |
              +-> canceled <-+
committed -> committed_cleanup_failed -> committed
canceled  -> canceled_cleanup_failed  -> canceled
```

Mapping may be changed while `reviewing`; doing so regenerates review output.
A commit against `committed` or `committed_cleanup_failed` is idempotent and
does not insert again. Other invalid transitions return `409 Conflict`.

Every import lookup uses both `ImportJob.id` and the authorized
`workspace_id`. A guessed import ID from another workspace therefore returns
404 before its status, filename, mapping, or checksum is revealed.

## Private file storage and retention

`LocalUploadStore` is rooted at `Settings.upload_directory`, defaulting to
`data/uploads`. It returns an opaque relative key such as
`42/8a17f2c4.csv`; it never stores the submitted filename and never accepts a
user-controlled filesystem path. Resolution verifies that the final path stays
inside the configured root.

Upload rules:

- accept a filename ending in `.csv` and a content type of `text/csv`,
  `application/csv`, `application/vnd.ms-excel`, or
  `application/octet-stream` (some browsers use the last two);
- read in 64 KiB chunks and stop once content exceeds 5 MiB;
- compute SHA-256 while writing;
- use a random server-generated name and create workspace directories as
  needed;
- delete a partial file on any validation or database failure;
- never log submitted filenames, file content, storage keys, checksums, or
  financial values.

The upload form offers `delete_after_import` and `retain`; the former is
selected by default. The accepted source must remain until commit because
review reparses it. On a successful database commit, deletion occurs after the
database transaction so a filesystem error cannot roll back approved
transactions. A successful deletion sets `UploadedFile.deleted = True`. A
failure changes the job to `committed_cleanup_failed`, records only a safe
error code in `validation_errors`, and shows the cleanup-retry form.

Cancellation always attempts deletion because there is no successful import
to audit. A failed cancellation cleanup uses `canceled_cleanup_failed`. Raw
retention does not create a download endpoint. Deleted records keep their
checksum and size metadata but their source path is no longer usable.

## CSV decoding, mapping, and normalization

The parser accepts UTF-8 and UTF-8 with a byte-order mark. It rejects NUL bytes,
invalid UTF-8, an empty file, missing/blank/duplicate headers, unsupported
delimiters, more than 50 columns, rows wider than the header, more than 1,000
data rows, or fields longer than 2,000 characters. Blank lines are ignored.

The mapping JSON stored on `ImportJob.column_mapping` has this exact shape:

```json
{
  "date_column": "Date",
  "description_column": "Description",
  "amount_mode": "single",
  "amount_column": "Amount",
  "debit_column": null,
  "credit_column": null,
  "date_format": "mdy",
  "amount_sign": "as_is"
}
```

`amount_mode` is `single` or `split`. Single mode requires `amount_column` and
uses `amount_sign` of `as_is` or `invert`. Split mode requires distinct debit
and credit columns, ignores `amount_sign`, makes debit negative and credit
positive, and rejects a row where both contain non-zero values. Date format is
one of `iso` (`YYYY-MM-DD`), `mdy` (`MM/DD/YYYY`), or `dmy`
(`DD/MM/YYYY`). Explicit date format avoids ambiguous values such as `01/02`.
No two logical fields may map to the same header.

Normalization is deterministic:

- parse money with `Decimal`, remove surrounding whitespace, one currency
  symbol (`$`), grouping commas, and accounting parentheses, require at most
  two decimal places, reject non-finite/zero values, and convert exactly to
  signed integer cents;
- parse a calendar date with the selected format and store it as midnight UTC
  because the existing `Transaction.date` column is a timezone-aware
  `DateTime`;
- normalize Unicode with NFKC, trim and collapse whitespace, reject a blank or
  over-512-character description, and store that cleaned value as
  `Transaction.description`;
- set `normalized_merchant` to the cleaned description uppercased. More
  opinionated merchant aliases and saved rules remain PR 5;
- assign the built-in `Uncategorized` category and set
  `categorization_source = "uncategorized"`.

The review table makes date, description, and amount editable and gives each
non-duplicate row an `include` checkbox selected by default. A user can correct
an invalid row or uncheck it. Existing duplicates are visibly locked out.
Commit reparses and validates every included edit; JavaScript is not trusted
for validation. At least one valid, non-duplicate row is required.

## Duplicate model

Duplicate checks have two layers, both scoped to the authorized workspace.

1. **Exact source:** before creating a second job, compare SHA-256 against a
   committed job's `source_checksum`. Delete the just-written copy and show a
   link to transactions rather than creating another import. An active matching
   job redirects to its current mapping/review step.
2. **Normalized transaction:** calculate a SHA-256 fingerprint from
   `date`, signed `amount_cents`, normalized merchant, and an occurrence number
   among identical rows in that source. The occurrence number allows two real,
   identical same-day charges in one statement while producing the same
   fingerprints when that statement is uploaded again.

The fingerprint payload is versioned and unambiguous:

```text
v1\nYYYY-MM-DD\n<signed-cents>\n<NFKC-uppercase-merchant>\n<occurrence>
```

Rows receive occurrence numbers in source row order, starting at 1 for each
identical `(date, amount_cents, normalized_merchant)` key. This is a pragmatic
duplicate heuristic, not proof that two bank events are identical. PR 4 shows
which rows it excluded and never silently deletes existing data.

Review queries existing fingerprints in chunks small enough for SQLite. Commit
recalculates them from posted edits inside the same unit of work. The existing
unique constraint on `(workspace_id, duplicate_fingerprint)` is the final race
condition guard. If another request wins concurrently, the whole transaction
rolls back and the user receives a conflict/review page; partial commits are
not allowed. The same fingerprint remains legal in another workspace.

## Built-in categories

A data-only Alembic revision `0006_builtin_categories` inserts these global
categories with `workspace_id = NULL`:

| Name | Kind |
| --- | --- |
| Uncategorized | expense |
| Groceries | expense |
| Dining | expense |
| Housing | expense |
| Utilities | expense |
| Transportation | expense |
| Shopping | expense |
| Entertainment | expense |
| Health | expense |
| Income | income |
| Transfers | transfer |

The migration selects before inserting so development databases that already
contain an identical global row do not receive a duplicate. Its downgrade
deletes only global rows with these exact name/kind pairs. PR 4 assigns only
`Uncategorized`; the other built-ins populate filters and prepare PR 5 without
claiming automatic categorization.

## Transaction list and filters

The list route always filters by authorized `workspace_id`, orders by
`Transaction.date DESC, Transaction.id DESC`, and returns 50 rows per page.
Supported query parameters are:

- `start_date` and `end_date`, inclusive calendar dates;
- `category_id`, limited to a global category or a category owned by the same
  workspace;
- `direction`: `all`, `expense` (`amount_cents < 0`), or `income`
  (`amount_cents > 0`);
- `q`, a trimmed case-insensitive contains search over description and
  normalized merchant, limited to 100 characters with SQL wildcard escaping;
- `page`, a positive integer defaulting to 1.

Invalid dates, an end before a start, an unsupported direction, an inaccessible
category, an overlong search, or a non-positive page render a helpful `422`
filter error without executing an unbounded query. The template preserves
filters in pagination links, displays the active workspace, and explains that
negative amounts are money out and positive amounts are money in. It does not
add summaries, charts, editing, deletion, or categorization controls.

## Error handling and privacy guarantees

- Unauthenticated requests follow PR 3's sign-in behavior.
- Unauthorized workspace/import/category IDs return 404 without existence
  details.
- CSRF failure occurs before file or database mutation.
- Invalid uploads and mappings render field-level errors; rejected or partial
  files are deleted.
- A missing source before commit marks the workflow unusable and asks for a
  new upload; it never creates transactions from stale form fields alone.
- A database error rolls back all candidate transactions and leaves the source
  available for retry.
- A post-commit cleanup error does not lie about the database commit; it shows
  the privacy cleanup warning and a retry action.
- Templates never render raw file content, storage keys, checksums, session
  data, or another workspace's values.
- Logs contain IDs, state names, row counts, and safe error codes only; PR 9
  will add fuller structured/redacted logging.

## Test strategy

Tests use synthetic names and amounts only. They are layered so beginners can
locate failures:

1. parser tests for encoding, dialect, header, row, field, and size limits;
2. mapping/normalization tests for date formats, amount modes/signs,
   descriptions, and integer cents;
3. fingerprint tests for stable re-upload behavior, repeated identical rows,
   edits, and cross-workspace allowance;
4. storage tests for opaque paths, size enforcement, partial cleanup,
   retention, and traversal rejection;
5. service tests for job transitions, atomic commit, duplicate exclusion,
   idempotency, rollback, and cleanup-failure recovery;
6. query tests for every filter, category authorization, ordering, pagination,
   and strict workspace isolation;
7. route tests with PR 3 dependencies overridden for unauthenticated,
   unauthorized, CSRF, invalid form, and happy-path behavior;
8. one acceptance test that uploads the sample, maps, reviews, edits/excludes,
   commits, lists, filters, and safely re-uploads it.

Final verification runs Ruff lint/format, all pytest tests, a fresh Alembic
upgrade, downgrade to 0005, and re-upgrade to 0006. No live Google, real bank
statement, network request, or Docker daemon is required in tests.

## Dependency and blocking matrix

| Work item | Safe to design/unit-test from PR2e | Blocked until PR3 merges |
| --- | --- | --- |
| CSV parser, mapping validator, normalization, fingerprints | Yes | No |
| Local upload-store adapter | Yes | No |
| Built-in-category migration design | Yes | No |
| Workspace-scoped SQLAlchemy query/service design | Yes | Actual PR3 fixtures/contracts must be confirmed |
| Import and transaction routes | No | Yes: user/workspace dependencies |
| Upload, mapping, commit, cancel, cleanup forms | No | Yes: CSRF and base-template context |
| Cross-workspace route tests | No | Yes: PR3 authorization behavior/fixtures |
| End-to-end upload-to-list acceptance test | No | Yes: sessions, CSRF, workspace navigation |
| Production PR 4 branch/PR | No | Yes: it must branch from merged PR3 |

This planning task intentionally writes none of the independently testable
production pieces. That avoids a later rebase carrying partial PR 4 code with
assumed auth interfaces.

## Post-PR3 execution handoff

After PR 3 merges:

1. Update local `main` and verify its merge commit includes PR 3.
2. Create `codex/pr-4-csv-import-transactions` from that merged commit.
3. Read PR 3's actual auth, workspace, CSRF, template, and test-fixture modules.
4. Fill out a contract check: unauthenticated behavior, 404-on-unauthorized,
   owner/member equality, dependency override hooks, CSRF field/header names,
   and base-template context.
5. Update only interface names in this spec/plan if PR 3 differs; do not change
   privacy behavior.
6. Run PR 3's full baseline checks before the first failing PR 4 test.
7. Execute the implementation plan task by task using TDD and small commits.
8. Open a production PR only after all route isolation, duplicate, retention,
   migration, lint, format, and acceptance checks pass.

## Self-review result

The design contains no unresolved placeholders. Scope matches the roadmap:
CSV import and transaction browsing are included; PR 3 auth, PR 5 rules, and
PR 8b account UI remain outside. File lifecycle, duplicate semantics, mapping
shape, job states, route authorization, built-in categories, filters, error
states, and the PR 3 interface are explicit enough to drive a TDD plan.
