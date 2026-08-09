# PR 2e Accounts and Balance Snapshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-owned accounts, dated balance snapshots, and optional account-targeted imports with a reversible SQLite migration and beginner-friendly documentation.

**Architecture:** Extend the existing single SQLAlchemy model module and linear Alembic migration chain. Keep account types and snapshot sources as flexible strings, store balances as integer cents, and use `is_liability` to determine future net-worth subtraction. Exercise the ORM through in-memory SQLite tests and exercise the migration chain against a temporary file-backed SQLite database.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, SQLite, pytest, Ruff, GitHub Actions, Docker Compose.

## Global Constraints

- Normal asset balances and amounts owed are positive integer cents; `Account.is_liability` supplies the net-worth direction.
- Negative balances remain valid for exceptional cases such as overdrafts and overpaid liabilities.
- `account_type` and snapshot `source` are strings, not database enums or `CHECK` constraints.
- Every account and snapshot has a required `workspace_id`.
- Existing imports remain valid because `ImportJob.account_id` is nullable.
- Do not add UI, statement parsing, net-worth calculations, investment holdings, dependencies, Docker changes, or CI workflow changes.

---

### Task 1: Add account ORM behavior test-first

**Files:**
- Create: `tests/test_accounts.py`
- Modify: `app/db/models.py`

**Interfaces:**
- Consumes: existing `Workspace`, `UploadedFile`, and `ImportJob` SQLAlchemy models plus the `session` and `workspace` pytest fixtures.
- Produces: `Account` and `AccountBalanceSnapshot` models; `Workspace.accounts`; `Workspace.account_balance_snapshots`; `UploadedFile.account_balance_snapshots`; `ImportJob.account_id`; `ImportJob.account`; `Account.import_jobs`.

- [ ] **Step 1: Write the failing account and snapshot tests**

Create `tests/test_accounts.py` with tests that import the not-yet-created models and cover the approved relationships and balance convention:

```python
from datetime import date

from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    AccountBalanceSnapshot,
    ImportJob,
    UploadedFile,
    Workspace,
)


def test_account_roundtrip(session: Session, workspace: Workspace) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Everyday Checking",
        account_type="checking",
        institution="Example Credit Union",
        is_liability=False,
    )
    session.add(account)
    session.commit()

    fetched = session.get(Account, account.id)
    assert fetched is not None
    assert fetched.name == "Everyday Checking"
    assert fetched.account_type == "checking"
    assert fetched.institution == "Example Credit Union"
    assert fetched.is_liability is False
    assert fetched.workspace.id == workspace.id


def test_liability_balance_uses_positive_amount_owed(
    session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Home Mortgage",
        account_type="mortgage",
        institution="Example Bank",
        is_liability=True,
    )
    session.add(account)
    session.commit()

    snapshot = AccountBalanceSnapshot(
        workspace_id=workspace.id,
        account_id=account.id,
        balance_cents=20_000_000,
        as_of_date=date(2026, 8, 1),
        source="manual",
    )
    session.add(snapshot)
    session.commit()

    fetched = session.get(AccountBalanceSnapshot, snapshot.id)
    assert fetched is not None
    assert fetched.balance_cents == 20_000_000
    assert fetched.account.is_liability is True
    assert fetched.workspace.id == workspace.id
    assert fetched in account.balance_snapshots


def test_exceptional_negative_balance_is_allowed(
    session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Overdrawn Checking",
        account_type="checking",
        is_liability=False,
    )
    session.add(account)
    session.commit()

    snapshot = AccountBalanceSnapshot(
        workspace_id=workspace.id,
        account_id=account.id,
        balance_cents=-5_000,
        as_of_date=date(2026, 8, 1),
    )
    session.add(snapshot)
    session.commit()

    assert snapshot.balance_cents == -5_000
    assert snapshot.source == "manual"


def test_multiple_snapshots_on_same_date_are_allowed(
    session: Session, workspace: Workspace
) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Brokerage",
        account_type="investment_brokerage",
        is_liability=False,
    )
    session.add(account)
    session.commit()

    snapshots = [
        AccountBalanceSnapshot(
            workspace_id=workspace.id,
            account_id=account.id,
            balance_cents=100_000,
            as_of_date=date(2026, 8, 1),
            source="manual",
        ),
        AccountBalanceSnapshot(
            workspace_id=workspace.id,
            account_id=account.id,
            balance_cents=101_000,
            as_of_date=date(2026, 8, 1),
            source="statement_import",
        ),
    ]
    session.add_all(snapshots)
    session.commit()

    assert all(snapshot.id is not None for snapshot in snapshots)


def test_snapshot_can_reference_uploaded_file(
    session: Session, workspace: Workspace
) -> None:
    uploaded_file = UploadedFile(
        workspace_id=workspace.id,
        file_type="pdf",
        storage_path="data/uploads/mortgage.pdf",
        checksum="c" * 64,
        size_bytes=2048,
    )
    account = Account(
        workspace_id=workspace.id,
        name="Home Mortgage",
        account_type="mortgage",
        is_liability=True,
    )
    session.add_all([uploaded_file, account])
    session.commit()

    snapshot = AccountBalanceSnapshot(
        workspace_id=workspace.id,
        account_id=account.id,
        balance_cents=20_000_000,
        as_of_date=date(2026, 8, 1),
        source="statement_import",
        uploaded_file_id=uploaded_file.id,
    )
    session.add(snapshot)
    session.commit()

    assert snapshot.uploaded_file is uploaded_file
    assert snapshot in uploaded_file.account_balance_snapshots


def test_import_job_can_target_account(session: Session, workspace: Workspace) -> None:
    account = Account(
        workspace_id=workspace.id,
        name="Everyday Checking",
        account_type="checking",
        is_liability=False,
    )
    session.add(account)
    session.commit()

    job = ImportJob(workspace_id=workspace.id, account_id=account.id)
    session.add(job)
    session.commit()

    assert job.account is account
    assert job in account.import_jobs


def test_balance_snapshot_composite_indexes() -> None:
    index_names = {index.name for index in AccountBalanceSnapshot.__table__.indexes}
    assert "ix_workspace_balance_snapshot_date" in index_names
    assert "ix_account_balance_snapshot_date" in index_names
```

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_accounts.py -v
```

Expected: collection fails because `Account` and `AccountBalanceSnapshot` do not exist yet.

- [ ] **Step 3: Add the SQLAlchemy fields and relationships**

In `app/db/models.py`:

1. Add these collection relationships to `Workspace`:

```python
accounts: Mapped[list["Account"]] = relationship(back_populates="workspace")
account_balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
    back_populates="workspace"
)
```

2. Add this relationship to `UploadedFile`:

```python
account_balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
    back_populates="uploaded_file"
)
```

3. Add the nullable account foreign key and relationship to `ImportJob`:

```python
account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
account: Mapped["Account | None"] = relationship(back_populates="import_jobs")
```

4. Define the two new models using these exact fields and index names:

```python
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(50))
    institution: Mapped[str | None] = mapped_column(String(255))
    is_liability: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="accounts")
    balance_snapshots: Mapped[list["AccountBalanceSnapshot"]] = relationship(
        back_populates="account"
    )
    import_jobs: Mapped[list["ImportJob"]] = relationship(back_populates="account")


class AccountBalanceSnapshot(Base):
    __tablename__ = "account_balance_snapshots"
    __table_args__ = (
        Index("ix_workspace_balance_snapshot_date", "workspace_id", "as_of_date"),
        Index("ix_account_balance_snapshot_date", "account_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    balance_cents: Mapped[int] = mapped_column()
    as_of_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    uploaded_file_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_files.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="account_balance_snapshots")
    account: Mapped["Account"] = relationship(back_populates="balance_snapshots")
    uploaded_file: Mapped["UploadedFile | None"] = relationship(
        back_populates="account_balance_snapshots"
    )
```

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
& .\.venv\Scripts\pytest.exe tests\test_accounts.py -v
& .\.venv\Scripts\pytest.exe
```

Expected: all new tests pass and the existing 26 tests remain green.

- [ ] **Step 5: Commit the ORM behavior**

```powershell
git add app/db/models.py tests/test_accounts.py
git commit -m "feat: add accounts and balance snapshots"
```

---

### Task 2: Add the reversible Alembic migration

**Files:**
- Create: `migrations/versions/0005_accounts_balances.py`

**Interfaces:**
- Consumes: database tables through revision `0004_planning_insights` and the table definitions from Task 1.
- Produces: revision `0005_accounts_balances`, which creates `accounts`, `account_balance_snapshots`, and nullable `import_jobs.account_id`.

- [ ] **Step 1: Demonstrate that the database is still at revision 0004**

Run Alembic against a fresh temporary SQLite path and inspect the log:

```powershell
$dbPath = Join-Path ([System.IO.Path]::GetTempPath()) ('wimm-pr2e-red-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($dbPath -replace '\\','/')
& .\.venv\Scripts\alembic.exe upgrade head
& .\.venv\Scripts\alembic.exe current
Remove-Item -LiteralPath $dbPath
```

Expected: Alembic reports `0004_planning_insights (head)`, proving no migration yet creates the new tables.

- [ ] **Step 2: Create revision 0005**

Create `migrations/versions/0005_accounts_balances.py` with revision metadata:

```python
revision = "0005_accounts_balances"
down_revision = "0004_planning_insights"
branch_labels = None
depends_on = None
```

Its `upgrade()` must:

- create `accounts` with the same types, nullability, defaults, timestamps, workspace foreign key, and `ix_accounts_workspace_id` index as the ORM model;
- create `account_balance_snapshots` with the same types, nullability, `manual` source default, foreign keys, timestamp, and both required composite indexes;
- use `op.batch_alter_table("import_jobs")` to add nullable `account_id` and named foreign key `fk_import_jobs_account_id_accounts`.

Its `downgrade()` must use `batch_alter_table` to drop the named foreign key and `account_id`, then drop `account_balance_snapshots` and `accounts`.

- [ ] **Step 3: Verify upgrade, downgrade, and re-upgrade**

Run:

```powershell
$dbPath = Join-Path ([System.IO.Path]::GetTempPath()) ('wimm-pr2e-green-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($dbPath -replace '\\','/')
try {
    & .\.venv\Scripts\alembic.exe upgrade head
    & .\.venv\Scripts\alembic.exe downgrade 0004_planning_insights
    & .\.venv\Scripts\alembic.exe upgrade head
} finally {
    Remove-Item -LiteralPath $dbPath -ErrorAction SilentlyContinue
}
```

Expected: all five upgrades run, revision 0005 downgrades cleanly, and revision 0005 re-applies cleanly.

- [ ] **Step 4: Commit the migration**

```powershell
git add migrations/versions/0005_accounts_balances.py
git commit -m "feat: migrate accounts and balance snapshots"
```

---

### Task 3: Refresh the README and run release-quality verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the completed PR2e schema and existing beginner-oriented README.
- Produces: accurate project-status documentation without changing Docker or CI configuration.

- [ ] **Step 1: Update outdated README status**

Replace the opening claim that the repository contains only the first PR with a short statement that the application foundation and database schema now exist, while user-facing finance workflows are still future work. In `Project map`, add:

```text
    app/db/models.py         SQLAlchemy table definitions and relationships
    migrations/             Ordered Alembic database schema changes
```

Replace `Next step` with a statement that PR 3 adds Google sign-in and workspace authorization, while the current database schema stores no data until application features use it.

- [ ] **Step 2: Run all quality gates**

Run:

```powershell
& .\.venv\Scripts\ruff.exe check .
& .\.venv\Scripts\ruff.exe format --check .
& .\.venv\Scripts\pytest.exe
```

Expected: Ruff passes, every file is formatted, and all 33 tests pass.

- [ ] **Step 3: Re-run the fresh migration cycle**

Repeat the Task 2 upgrade/downgrade/re-upgrade command after all edits.

Expected: the full migration chain is reversible and finishes at `0005_accounts_balances`.

- [ ] **Step 4: Inspect the final diff and commit documentation**

Run `git diff --check`, inspect `git diff`, then commit:

```powershell
git add README.md
git commit -m "docs: update database project status"
```

---

### Task 4: Review and create the pull request

**Files:**
- Review only: all commits after `main`

**Interfaces:**
- Consumes: verified PR2e commits.
- Produces: pushed branch and GitHub pull request targeting `main`.

- [ ] **Step 1: Review scope and history**

Run:

```powershell
git status --short --branch
git log --oneline main..HEAD
git diff --stat main...HEAD
git diff main...HEAD
```

Expected: only the design, plan, models, tests, migration, and README changes are present.

- [ ] **Step 2: Push the feature branch**

```powershell
git push -u origin codex/pr-2e-accounts-balances
```

- [ ] **Step 3: Create the PR**

Create a ready-for-review pull request targeting `main` with title
`PR 2e — accounts and balance snapshots`. The body must summarize the new
models and relationships, reversible migration, balance convention, tests,
README correction, and the exact verification results.
