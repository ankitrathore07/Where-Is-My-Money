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
    """An account belongs to a workspace and keeps its identifying details."""
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
    """A liability stores a normal amount owed as positive integer cents."""
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


def test_exceptional_negative_balance_is_allowed(session: Session, workspace: Workspace) -> None:
    """An exceptional balance such as an overdraft may remain negative."""
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
    """Corrections may create multiple snapshots for one account and date."""
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


def test_snapshot_can_reference_uploaded_file(session: Session, workspace: Workspace) -> None:
    """An imported balance keeps a relationship to its source file."""
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
    """An import job may optionally target a specific account."""
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
    """Snapshot queries have both required workspace/date and account/date indexes."""
    index_names = {index.name for index in AccountBalanceSnapshot.__table__.indexes}
    assert "ix_workspace_balance_snapshot_date" in index_names
    assert "ix_account_balance_snapshot_date" in index_names
