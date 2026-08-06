from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import IncomeRecord, Payslip, Transaction, UploadedFile


def test_payslip_roundtrip(session: Session, workspace) -> None:
    """A payslip stores employer, pay period, candidate fields, and confidence."""
    slip = Payslip(
        workspace_id=workspace.id,
        employer="Acme Corp",
        pay_period_start=datetime(2026, 1, 1),
        pay_period_end=datetime(2026, 1, 15),
        pay_date=datetime(2026, 1, 20),
        candidate_fields={"gross": 500000, "net": 380000, "taxes": 90000},
        confidence=0.92,
        review_status="pending",
    )
    session.add(slip)
    session.commit()

    fetched = session.get(Payslip, slip.id)
    assert fetched is not None
    assert fetched.employer == "Acme Corp"
    assert fetched.confidence == 0.92
    assert fetched.candidate_fields["net"] == 380000
    assert fetched.workspace is not None


def test_payslip_with_optional_uploaded_file(session: Session, workspace) -> None:
    """A payslip can optionally reference an uploaded file."""
    f = UploadedFile(
        workspace_id=workspace.id,
        file_type="pdf",
        storage_path="data/uploads/pay.pdf",
        checksum="c" * 64,
        size_bytes=2048,
    )
    session.add(f)
    session.commit()

    slip = Payslip(
        workspace_id=workspace.id,
        uploaded_file_id=f.id,
        employer="Acme Corp",
        pay_date=datetime(2026, 1, 20),
        review_status="pending",
    )
    session.add(slip)
    session.commit()

    assert slip.uploaded_file is not None
    assert slip.uploaded_file.file_type == "pdf"


def test_income_record_integer_cents(session: Session, workspace) -> None:
    """Income amounts are stored as signed integer cents, never floats."""
    slip = Payslip(
        workspace_id=workspace.id,
        employer="Acme Corp",
        pay_date=datetime(2026, 1, 20),
        review_status="confirmed",
    )
    session.add(slip)
    session.commit()

    rec = IncomeRecord(
        workspace_id=workspace.id,
        payslip_id=slip.id,
        employer="Acme Corp",
        pay_date=datetime(2026, 1, 20),
        gross_pay_cents=500000,
        net_pay_cents=380000,
        taxes_cents=90000,
        deductions_cents=30000,
    )
    session.add(rec)
    session.commit()

    fetched = session.get(IncomeRecord, rec.id)
    assert fetched is not None
    assert fetched.gross_pay_cents == 500000
    assert isinstance(fetched.gross_pay_cents, int)
    assert isinstance(fetched.net_pay_cents, int)
    assert fetched.payslip is not None


def test_income_record_without_payslip(session: Session, workspace) -> None:
    """An income record can be manually entered without a payslip."""
    rec = IncomeRecord(
        workspace_id=workspace.id,
        employer="Freelance",
        pay_date=datetime(2026, 1, 20),
        gross_pay_cents=200000,
        net_pay_cents=200000,
        taxes_cents=0,
        deductions_cents=0,
    )
    session.add(rec)
    session.commit()

    assert rec.id is not None
    assert rec.payslip is None


def test_income_records_separate_from_transactions(session: Session, workspace) -> None:
    """Income records have no link to transactions — direct deposits are not
    duplicated as transactions.

    This is a core design rule from the plan: income_records stay separate
    from bank transactions to avoid double-counting a paycheck deposit.
    """
    slip = Payslip(
        workspace_id=workspace.id,
        employer="Acme Corp",
        pay_date=datetime(2026, 1, 20),
        review_status="confirmed",
    )
    session.add(slip)
    session.commit()

    rec = IncomeRecord(
        workspace_id=workspace.id,
        payslip_id=slip.id,
        pay_date=datetime(2026, 1, 20),
        gross_pay_cents=500000,
        net_pay_cents=380000,
        taxes_cents=90000,
        deductions_cents=30000,
    )
    # A bank transaction for the same direct deposit — independent row.
    tx = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 1, 20),
        description="DIRECT DEP ACME CORP",
        amount_cents=380000,
        categorization_source="uncategorized",
    )
    session.add_all([rec, tx])
    session.commit()

    assert rec.id is not None
    assert tx.id is not None
    # No FK between them: income record has no transaction reference.
    assert not hasattr(rec, "transaction_id")
    assert not hasattr(tx, "income_record_id")
