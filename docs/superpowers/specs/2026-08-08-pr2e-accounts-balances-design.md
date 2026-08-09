# PR 2e — Accounts and Balance Snapshots Design

## Goal

Add the database foundation for accounts and point-in-time balances so a later
feature can import account statements and calculate net worth. PR 2e only adds
schema, relationships, migrations, tests, and a small README correction. It does
not add account screens, statement parsing, or net-worth calculations.

## Design decisions

- Match the existing SQLAlchemy style by storing `account_type` and `source` as
  strings instead of database enums or `CHECK` constraints. This keeps the
  schema easy to extend and consistent with fields such as import status.
- Store all money as integer cents. A normal asset balance and a normal amount
  owed are both positive. `Account.is_liability` determines whether a future
  net-worth calculation adds or subtracts the balance. Negative values remain
  valid for exceptional cases such as an overdrawn asset account or an
  overpaid liability.
- Keep a direct `workspace_id` on every financial record, including snapshots,
  to preserve the application's workspace privacy boundary and support
  workspace-scoped queries.
- Allow more than one snapshot for the same account and date. Imports may be
  corrected or repeated, and PR 2e has no product requirement to reject them.

## SQLAlchemy models

### Account

`Account` represents one asset or liability owned by a workspace.

- `id`: integer primary key
- `workspace_id`: required foreign key to `workspaces.id`
- `name`: required string, up to 255 characters
- `account_type`: required string, up to 50 characters
- `institution`: optional string, up to 255 characters
- `is_liability`: required boolean, default `False`
- `created_at` and `updated_at`: database-generated timestamps

Initial documented account types are `checking`, `savings`, `credit_card`,
`investment_401k`, `investment_brokerage`, `mortgage`, `auto_loan`,
`student_loan`, and `other`. They are documented values rather than a closed
database-level list.

Relationships connect an account to its workspace, balance snapshots, and
optional import jobs.

### AccountBalanceSnapshot

`AccountBalanceSnapshot` records an account balance at one point in time.

- `id`: integer primary key
- `workspace_id`: required foreign key to `workspaces.id`
- `account_id`: required foreign key to `accounts.id`
- `balance_cents`: required integer
- `as_of_date`: required date
- `source`: required string, up to 50 characters, default `manual`
- `uploaded_file_id`: optional foreign key to `uploaded_files.id`
- `created_at`: database-generated timestamp

Initial documented sources are `manual` and `statement_import`. The optional
file relationship preserves evidence for an imported balance without requiring
a file for manual entries.

Relationships connect a snapshot to its workspace, account, and optional
uploaded file.

### Existing model changes

- Add nullable `ImportJob.account_id` referencing `accounts.id` so an import can
  target a specific account without breaking existing transaction imports.
- Add the matching `ImportJob.account` and `Account.import_jobs` relationships.
- Add collection relationships from `Workspace` to accounts and balance
  snapshots, and from `UploadedFile` to balance snapshots.

PR 2e follows existing schema conventions and does not introduce a composite
foreign key that proves an account and snapshot share a workspace. Application
authorization will select both records within the active workspace, as it does
for the project's other workspace-owned relationships.

## Migration

Create Alembic revision `0005_accounts_balances` with
`down_revision = "0004_planning_insights"`.

The upgrade will:

1. Create `accounts`.
2. Create `account_balance_snapshots`.
3. Add nullable `account_id` to `import_jobs` with its foreign key.
4. Create the required indexes:
   - `ix_workspace_balance_snapshot_date` on
     `(workspace_id, as_of_date)`
   - `ix_account_balance_snapshot_date` on `(account_id, as_of_date)`

The migration will use Alembic's SQLite-compatible `batch_alter_table` operation
to add `import_jobs.account_id` and a named
`fk_import_jobs_account_id_accounts` foreign key. The downgrade uses the same
batch operation to remove that constraint and column, then drops snapshots and
accounts in dependency order.

## Validation and errors

SQLite and SQLAlchemy reject missing required fields and invalid foreign keys
when foreign-key enforcement is enabled. Optional links remain nullable so an
existing transaction import does not need an account and a manual snapshot does
not need an uploaded file. Account types and snapshot sources are validated by
future application forms and import workflows rather than by this schema PR.
Callers remain responsible for rolling back a SQLAlchemy session after an
`IntegrityError`, matching the existing test and session patterns.

## Tests and verification

Tests will be written before the model implementation and will prove:

- an asset account round-trips through a SQLAlchemy session;
- a liability account and positive amount-owed balance follow the agreed sign
  convention;
- a snapshot loads its account and workspace relationships;
- a snapshot may link to an uploaded source file;
- an import job may optionally target an account;
- the two required composite indexes exist in SQLAlchemy metadata.

Final verification will run Ruff linting, Ruff formatting, the complete pytest
suite, and Alembic against a fresh temporary SQLite database. The migration will
also be downgraded and upgraded again to catch SQLite alteration problems.

GitHub Actions requires no workflow change: its existing migration step will
automatically apply revision 0005 before running the tests. Docker requires no
change because the container already mounts persistent database storage and
runs the same application code.

## Documentation

Update the README so it no longer describes the application as waiting for PR
2. Briefly reflect that the database foundation now includes the planned
financial schema. Keep the fuller Python, Docker, and CI learning documentation
for PR 9, as already planned.

## Out of scope

- Account creation or editing pages
- CSV or PDF account-statement parsing
- Balance confirmation screens
- Net-worth calculations and charts
- Account-type validation at the database layer
- Individual investment holdings
- Unrelated Docker or CI refactoring
