from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Category,
    ImportJob,
    MerchantRule,
    Transaction,
    UploadedFile,
    User,
    Workspace,
)


def test_uploaded_file_roundtrip(session: Session, workspace: Workspace) -> None:
    """An uploaded file stores its type, path, checksum, and size."""
    f = UploadedFile(
        workspace_id=workspace.id,
        file_type="csv",
        storage_path="data/uploads/abc.csv",
        checksum="a" * 64,
        size_bytes=1024,
        retention_choice="retain",
        deleted=False,
    )
    session.add(f)
    session.commit()

    assert f.id is not None
    fetched = session.get(UploadedFile, f.id)
    assert fetched is not None
    assert fetched.checksum == "a" * 64
    assert fetched.workspace is not None


def test_import_job_links_to_uploaded_file(session: Session, workspace: Workspace) -> None:
    """An import job references its workspace and the uploaded source file."""
    f = UploadedFile(
        workspace_id=workspace.id,
        file_type="csv",
        storage_path="data/uploads/abc.csv",
        checksum="b" * 64,
        size_bytes=512,
    )
    session.add(f)
    session.commit()

    job = ImportJob(
        workspace_id=workspace.id,
        uploaded_file_id=f.id,
        status="reviewing",
        column_mapping={"date": "Date", "amount": "Debit"},
        validation_errors={"row_3": "negative amount"},
        source_checksum=f.checksum,
    )
    session.add(job)
    session.commit()

    assert job.id is not None
    assert job.uploaded_file is not None
    assert job.column_mapping["amount"] == "Debit"


def test_category_workspace_specific_and_builtin(session: Session, workspace: Workspace) -> None:
    """Categories can be workspace-specific (custom) or built-in (null workspace)."""
    custom = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    builtin = Category(workspace_id=None, name="Uncategorized", kind="expense")
    session.add_all([custom, builtin])
    session.commit()

    assert custom.id is not None
    assert builtin.id is not None
    assert custom.workspace is not None
    assert builtin.workspace is None


def test_transaction_signed_integer_cents(session: Session, workspace: Workspace) -> None:
    """Amounts are stored as signed integer cents, never floats."""
    cat = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    session.add(cat)
    session.commit()

    tx = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 15, 12, 0, 0),
        description="WHOLE FOODS MARKET",
        normalized_merchant="Whole Foods",
        amount_cents=-4299,
        category_id=cat.id,
        categorization_source="manual",
    )
    session.add(tx)
    session.commit()

    fetched = session.get(Transaction, tx.id)
    assert fetched is not None
    assert fetched.amount_cents == -4299
    assert isinstance(fetched.amount_cents, int)
    assert fetched.category is not None
    assert fetched.category.name == "Groceries"


def test_merchant_rule(session: Session, workspace: Workspace) -> None:
    """A merchant rule maps a pattern to a normalized name and category."""
    cat = Category(workspace_id=workspace.id, name="Groceries", kind="expense")
    session.add(cat)
    session.commit()

    rule = MerchantRule(
        workspace_id=workspace.id,
        merchant_pattern="WHOLE FOODS*",
        normalized_merchant="Whole Foods",
        category_id=cat.id,
    )
    session.add(rule)
    session.commit()

    assert rule.id is not None
    assert rule.category is not None
    assert rule.category.name == "Groceries"


def test_duplicate_fingerprint_rejects_duplicates(session: Session, workspace: Workspace) -> None:
    """The same fingerprint in the same workspace must be rejected."""
    tx1 = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 15),
        description="NETFLIX",
        amount_cents=-1599,
        duplicate_fingerprint="fp-001",
    )
    tx2 = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 15),
        description="NETFLIX",
        amount_cents=-1599,
        duplicate_fingerprint="fp-001",
    )
    session.add_all([tx1, tx2])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_duplicate_fingerprint_allows_null(session: Session, workspace: Workspace) -> None:
    """Multiple transactions without a fingerprint (NULL) are allowed."""
    tx1 = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 15),
        description="Cash",
        amount_cents=-2000,
        duplicate_fingerprint=None,
    )
    tx2 = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 16),
        description="Cash",
        amount_cents=-500,
        duplicate_fingerprint=None,
    )
    session.add_all([tx1, tx2])
    session.commit()
    assert tx1.id is not None
    assert tx2.id is not None


def test_duplicate_fingerprint_cross_workspace_allowed(session: Session) -> None:
    """The same fingerprint in different workspaces is allowed (not a duplicate)."""
    owner = User(google_sub="sub-a", email="a@example.com")
    session.add(owner)
    session.commit()

    ws1 = Workspace(name="Personal A", is_personal=True, owner_id=owner.id)
    ws2 = Workspace(name="Personal B", is_personal=True, owner_id=owner.id)
    session.add_all([ws1, ws2])
    session.commit()

    tx1 = Transaction(
        workspace_id=ws1.id,
        date=datetime(2026, 1, 15),
        description="NETFLIX",
        amount_cents=-1599,
        duplicate_fingerprint="fp-shared",
    )
    tx2 = Transaction(
        workspace_id=ws2.id,
        date=datetime(2026, 1, 15),
        description="NETFLIX",
        amount_cents=-1599,
        duplicate_fingerprint="fp-shared",
    )
    session.add_all([tx1, tx2])
    session.commit()
    assert tx1.id is not None
    assert tx2.id is not None
    assert tx1.id != tx2.id
