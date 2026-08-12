from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    Account,
    AccountBalanceSnapshot,
    AccountStatementImport,
    UploadedFile,
    User,
    Workspace,
)
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_statement_routes_require_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/workspaces/1/accounts/1/statements/new", follow_redirects=False
            )
    finally:
        engine.dispose()
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_accounts_show_import_for_every_supported_account_type(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                account_definitions = (
                    ("Checking", "checking", False),
                    ("Savings", "savings", False),
                    ("Credit card", "credit_card", True),
                    ("401(k)", "investment_401k", False),
                    ("Brokerage", "investment_brokerage", False),
                    ("Mortgage", "mortgage", True),
                    ("Auto loan", "auto_loan", True),
                    ("Student loan", "student_loan", True),
                    ("Other", "other", False),
                )
                session.add_all(
                    Account(
                        workspace_id=workspace_id,
                        name=name,
                        account_type=account_type,
                        is_liability=is_liability,
                    )
                    for name, account_type, is_liability in account_definitions
                )
                session.commit()
                accounts = {
                    account.name: account.id for account in session.scalars(select(Account))
                }
            response = await client.get(f"/workspaces/{workspace_id}/accounts")
            upload_pages = {
                name: await client.get(
                    f"/workspaces/{workspace_id}/accounts/{account_id}/statements/new"
                )
                for name, account_id in accounts.items()
            }
    finally:
        engine.dispose()
    assert response.status_code == 200
    for account_id in accounts.values():
        assert f"/accounts/{account_id}/statements/new" in response.text
    assert all(page.status_code == 200 for page in upload_pages.values())
    assert 'value="bank_account"' in upload_pages["Checking"].text
    assert 'value="bank_account"' in upload_pages["Savings"].text
    assert 'value="credit_card"' in upload_pages["Credit card"].text
    assert 'value="investment_401k"' in upload_pages["401(k)"].text
    assert 'value="brokerage"' in upload_pages["Brokerage"].text
    assert 'value="mortgage"' in upload_pages["Mortgage"].text
    assert 'value="loan"' in upload_pages["Auto loan"].text
    assert 'value="loan"' in upload_pages["Student loan"].text
    assert 'value="other"' in upload_pages["Other"].text


@pytest.mark.anyio
async def test_member_uploads_reviews_edits_and_confirms_statement(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                account = Account(
                    workspace_id=workspace_id,
                    name="Brokerage",
                    account_type="investment_brokerage",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            token = client.cookies["wimm_csrf"]
            csv_bytes = (
                b"account_name,institution,account_last_four,total_balance,as_of_date\n"
                b"Northstar Brokerage,Fictional Brokerage,4821,125430.18,2026-07-31\n"
            )
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={
                    "csrf_token": token,
                    "statement_category": "brokerage",
                    "retention_choice": "retain",
                },
                files={"statement_file": ("statement.csv", csv_bytes, "text/csv")},
                follow_redirects=False,
            )
            assert uploaded.status_code == 303
            with factory() as session:
                pending = session.scalar(select(AccountStatementImport))
                assert pending is not None
                pending_id = pending.id
                assert session.scalar(select(AccountBalanceSnapshot.id)) is None
            review = await client.get(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/review"
            )
            assert "125430.18" in review.text
            assert "Brokerage" in review.text
            assert "WIMM balance CSV" in review.text
            confirmed = await client.post(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/confirm",
                data={
                    "csrf_token": token,
                    "account_id": str(account_id),
                    "account_name": "Reviewed Brokerage",
                    "institution": "Reviewed Institution",
                    "account_last_four": "4821",
                    "total_balance": "125000.01",
                    "as_of_date": "2026-07-31",
                },
                follow_redirects=False,
            )
            assert confirmed.status_code == 303
            assert confirmed.headers["location"] == f"/workspaces/{workspace_id}/dashboard"
            with factory() as session:
                snapshot = session.scalar(select(AccountBalanceSnapshot))
                assert snapshot is not None
                assert snapshot.balance_cents == 12_500_001
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_statement_mutations_require_csrf(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={"statement_category": "brokerage", "retention_choice": "retain"},
                files={"statement_file": ("statement.csv", b"data", "text/csv")},
            )
    finally:
        engine.dispose()
    assert response.status_code == 403


@pytest.mark.anyio
async def test_malformed_pdf_returns_safe_upload_validation(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
            response = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "statement_category": "brokerage",
                    "retention_choice": "retain",
                },
                files={
                    "statement_file": (
                        "statement.pdf",
                        b"%PDF-this-is-malformed",
                        "application/pdf",
                    )
                },
            )
    finally:
        engine.dispose()
    assert response.status_code == 400
    assert "valid PDF statement" in response.text


@pytest.mark.anyio
async def test_foreign_destination_account_confirmation_returns_404(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                foreign_user = User(google_sub="foreign-sub", email="foreign@example.com")
                foreign_workspace = Workspace(name="Foreign", is_personal=True, owner=foreign_user)
                foreign_account = Account(
                    workspace=foreign_workspace,
                    name="SECRET Mortgage",
                    account_type="mortgage",
                    is_liability=True,
                )
                uploaded_file = UploadedFile(
                    workspace_id=workspace.id,
                    file_type="account_statement",
                    storage_path=f"{workspace.id}/{'c' * 32}.csv",
                    checksum="c" * 64,
                    size_bytes=10,
                )
                pending = AccountStatementImport(
                    workspace_id=workspace.id,
                    uploaded_file=uploaded_file,
                    statement_category="mortgage",
                    source_checksum="c" * 64,
                    candidate_fields={
                        "account_name": "Mortgage",
                        "institution": None,
                        "account_last_four": "7742",
                        "balance_cents": 10000,
                        "as_of_date": "2026-07-31",
                        "extraction_method": "wimm_csv",
                    },
                    review_status="pending",
                )
                session.add_all([foreign_account, pending])
                session.commit()
                workspace_id = workspace.id
                pending_id = pending.id
                foreign_account_id = foreign_account.id
            response = await client.post(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/confirm",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "account_id": str(foreign_account_id),
                    "account_name": "Mortgage",
                    "institution": "",
                    "account_last_four": "7742",
                    "total_balance": "100.00",
                    "as_of_date": "2026-07-31",
                },
            )
    finally:
        engine.dispose()
    assert response.status_code == 404
    assert "SECRET Mortgage" not in response.text


@pytest.mark.anyio
async def test_cleanup_failure_warns_on_dashboard_and_retry_deletes_source(
    tmp_path: Path,
) -> None:
    from app.statement_imports.storage import StatementUploadStore

    class FailingDeleteStore(StatementUploadStore):
        def delete(self, storage_key: str) -> None:
            raise OSError("synthetic cleanup failure")

    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                account = Account(
                    workspace_id=workspace_id,
                    name="Brokerage",
                    account_type="investment_brokerage",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                account_id = account.id
            token = client.cookies["wimm_csrf"]
            csv_bytes = (
                b"account_name,institution,account_last_four,total_balance,as_of_date\n"
                b"Brokerage,Northstar,4821,100.00,2026-07-31\n"
            )
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/statement-imports",
                data={
                    "csrf_token": token,
                    "statement_category": "brokerage",
                    "retention_choice": "delete_after_import",
                },
                files={"statement_file": ("statement.csv", csv_bytes, "text/csv")},
                follow_redirects=False,
            )
            pending_id = int(uploaded.headers["location"].split("/")[-2])
            application.state.statement_store = FailingDeleteStore(tmp_path)
            confirmed = await client.post(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/confirm",
                data={
                    "csrf_token": token,
                    "account_id": str(account_id),
                    "account_name": "Brokerage",
                    "institution": "Northstar",
                    "account_last_four": "4821",
                    "total_balance": "100.00",
                    "as_of_date": "2026-07-31",
                },
                follow_redirects=False,
            )
            assert confirmed.headers["location"].endswith(f"statement_cleanup_failed={pending_id}")
            warning = await client.get(confirmed.headers["location"])
            assert "private statement source could not be deleted" in warning.text
            assert f"statement-imports/{pending_id}/cleanup" in warning.text

            application.state.statement_store = StatementUploadStore(tmp_path)
            retried = await client.post(
                f"/workspaces/{workspace_id}/statement-imports/{pending_id}/cleanup",
                data={"csrf_token": token},
                follow_redirects=False,
            )
            assert retried.status_code == 303
            with factory() as session:
                pending = session.get(AccountStatementImport, pending_id)
                assert pending is not None
                assert pending.review_status == "confirmed"
                assert pending.uploaded_file.deleted is True
    finally:
        engine.dispose()
