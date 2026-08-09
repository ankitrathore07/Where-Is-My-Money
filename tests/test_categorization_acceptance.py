from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    Category,
    MerchantRule,
    Transaction,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.imports.service import build_review, create_csv_import, save_mapping
from app.imports.storage import LocalUploadStore
from tests.route_helpers import build_route_test_app, complete_sign_in


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _mapped_cafe_job(session, store: LocalUploadStore, workspace: Workspace):
    created = create_csv_import(
        session,
        store,
        workspace,
        BytesIO(b"Date,Description,Amount\n08/10/2026,LOCAL CAFE 123,-18.00\n"),
        "retain",
    )
    save_mapping(
        session,
        store,
        created.job,
        {
            "date_column": "Date",
            "description_column": "Description",
            "amount_mode": "single",
            "amount_column": "Amount",
            "date_format": "mdy",
            "amount_sign": "as_is",
        },
    )
    return created.job


@pytest.mark.anyio
async def test_member_saved_rule_categorizes_later_import_only_in_its_workspace(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    store = LocalUploadStore(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                member = session.scalar(select(User).where(User.google_sub == "import-route-sub"))
                assert member is not None
                owner = User(google_sub="shared-owner", email="owner@example.com")
                shared = Workspace(name="Shared Home", is_personal=False, owner=owner)
                isolated = Workspace(name="Other Home", is_personal=True, owner=owner)
                session.add_all([shared, isolated])
                session.flush()
                session.add(
                    WorkspaceMembership(
                        workspace_id=shared.id,
                        user_id=member.id,
                        role="member",
                    )
                )
                transaction = Transaction(
                    workspace=shared,
                    date=datetime(2026, 8, 9, tzinfo=UTC),
                    description="LOCAL CAFE 123",
                    normalized_merchant="Local Cafe",
                    amount_cents=-2450,
                    categorization_source="uncategorized",
                )
                session.add(transaction)
                session.commit()
                shared_id = shared.id
                isolated_id = isolated.id
                transaction_id = transaction.id

            created_category = await client.post(
                f"/workspaces/{shared_id}/categories",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "name": "Date Night",
                    "kind": "expense",
                },
                follow_redirects=False,
            )
            assert created_category.status_code == 303

            with factory() as session:
                category = session.scalar(
                    select(Category).where(
                        Category.workspace_id == shared_id,
                        Category.name == "Date Night",
                    )
                )
                assert category is not None
                category_id = category.id

            page = await client.get(
                f"/workspaces/{shared_id}/transactions/{transaction_id}/categorization"
            )
            saved = await client.post(
                f"/workspaces/{shared_id}/transactions/{transaction_id}/categorization",
                data={
                    "csrf_token": client.cookies["wimm_csrf"],
                    "normalized_merchant": "Neighborhood Cafe",
                    "category_id": str(category_id),
                    "is_subscription": "on",
                    "save_for_future": "on",
                },
                follow_redirects=False,
            )

            assert page.status_code == 200
            assert "Date Night" in page.text
            assert saved.status_code == 303

            with factory() as session:
                shared = session.get(Workspace, shared_id)
                isolated = session.get(Workspace, isolated_id)
                current = session.get(Transaction, transaction_id)
                rule = session.scalar(
                    select(MerchantRule).where(MerchantRule.workspace_id == shared_id)
                )
                assert shared is not None and isolated is not None
                assert current is not None and rule is not None
                assert current.categorization_source == "manual"
                assert current.category_id == category_id
                assert current.is_subscription is True

                shared_row = build_review(
                    session,
                    store,
                    _mapped_cafe_job(session, store, shared),
                ).rows[0]
                isolated_row = build_review(
                    session,
                    store,
                    _mapped_cafe_job(session, store, isolated),
                ).rows[0]

                assert shared_row.normalized_merchant == "Neighborhood Cafe"
                assert shared_row.category_id == category_id
                assert shared_row.is_subscription is True
                assert shared_row.categorization_source == "workspace_rule"
                assert isolated_row.category_name == "Uncategorized"
                assert isolated_row.is_subscription is False
                assert isolated_row.categorization_source == "uncategorized"
    finally:
        engine.dispose()
