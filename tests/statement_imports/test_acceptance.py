from datetime import date
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from app.dashboard.service import build_dashboard_report
from app.db.models import Account, Workspace
from app.payslips.extraction import ExtractedText
from app.statement_imports.service import confirm_statement_import, ingest_one_statement
from app.statement_imports.storage import StatementUploadStore


class UnusedExtractor:
    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        raise AssertionError("Synthetic CSV statements do not use document extraction")


def _csv(name: str, balance: str) -> bytes:
    return (
        "account_name,institution,account_last_four,total_balance,as_of_date\n"
        f"{name},Northstar Financial,4821,{balance},2026-07-31\n"
    ).encode()


def test_all_supported_account_types_update_dashboard_only_after_confirmation(
    tmp_path: Path, session: Session, workspace: Workspace
) -> None:
    definitions = (
        ("bank_account", "Checking", "checking", False, "5000.00"),
        ("bank_account", "Savings", "savings", False, "15000.00"),
        ("credit_card", "Credit card", "credit_card", True, "2500.00"),
        ("investment_401k", "401(k)", "investment_401k", False, "100000.00"),
        ("brokerage", "Brokerage", "investment_brokerage", False, "50000.00"),
        ("mortgage", "Mortgage", "mortgage", True, "200000.00"),
        ("loan", "Auto loan", "auto_loan", True, "20000.00"),
        ("loan", "Student loan", "student_loan", True, "10000.00"),
        ("other", "Other asset", "other", False, "10000.00"),
    )
    accounts: dict[str, Account] = {}
    for _category, name, account_type, is_liability, _ in definitions:
        account = Account(
            workspace_id=workspace.id,
            name=name,
            account_type=account_type,
            is_liability=is_liability,
        )
        session.add(account)
        accounts[name] = account
    session.commit()
    store = StatementUploadStore(tmp_path)
    pending_imports = []
    for category, name, _, _, balance in definitions:
        pending_imports.append(
            (
                name,
                balance,
                ingest_one_statement(
                    session,
                    store,
                    UnusedExtractor(),
                    workspace,
                    category,
                    f"{category}.csv",
                    "text/csv",
                    BytesIO(_csv(name, balance)),
                    "retain",
                ),
            )
        )

    before = build_dashboard_report(session, workspace.id, date(2026, 7, 31))
    assert before.position.assets_cents == 0
    assert before.position.liabilities_cents == 0
    assert before.position.missing_balance_count == 9

    for name, balance, pending in pending_imports:
        candidate = pending.candidate_fields
        confirm_statement_import(
            session,
            store,
            pending,
            {
                "account_id": str(accounts[name].id),
                "account_name": str(candidate["account_name"]),
                "institution": str(candidate["institution"]),
                "account_last_four": str(candidate["account_last_four"]),
                "total_balance": balance,
                "as_of_date": str(candidate["as_of_date"]),
            },
            today=date(2026, 8, 11),
        )

    after = build_dashboard_report(session, workspace.id, date(2026, 7, 31))
    assert after.position.assets_cents == 18_000_000
    assert after.position.liabilities_cents == 23_250_000
    assert after.position.net_worth_cents == -5_250_000
    assert after.position.missing_balance_count == 0
    assert {position.name for position in after.position.accounts} == {
        "401(k)",
        "Checking",
        "Savings",
        "Credit card",
        "Brokerage",
        "Mortgage",
        "Auto loan",
        "Student loan",
        "Other asset",
    }
