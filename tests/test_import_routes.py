from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.categorization.ai_graph import build_categorization_graph
from app.categorization.ai_types import ClassifierResult
from app.db.models import (
    Account,
    Category,
    ImportJob,
    Tag,
    Transaction,
    UploadedFile,
    User,
    Workspace,
)
from tests.route_helpers import (
    build_route_test_app,
    complete_sign_in,
    csrf_token,
    review_token,
    verified_claims,
)

CSV_BYTES = b"Date,Description,Amount\n08/01/2026,Example Market,-12.34\n"


class ShoppingClassifier:
    def classify(self, description: str, allowed_categories: tuple[str, ...]) -> ClassifierResult:
        assert description == "EXAMPLE MARKET"
        assert allowed_categories == ("Shopping",)
        return ClassifierResult("Shopping", False, False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_new_import_requires_authentication(tmp_path: Path) -> None:
    application, _, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/workspaces/1/imports/new", follow_redirects=False)
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.anyio
async def test_import_form_lists_workspace_transaction_accounts(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                checking = Account(
                    workspace_id=workspace.id,
                    name="Chase Checking",
                    account_type="checking",
                    institution_key="chase",
                    institution="Chase",
                    is_liability=False,
                )
                mortgage = Account(
                    workspace_id=workspace.id,
                    name="Mortgage",
                    account_type="mortgage",
                    institution_key="other",
                    institution="Other",
                    is_liability=True,
                )
                session.add_all((checking, mortgage))
                session.commit()
                workspace_id = workspace.id
                checking_id = checking.id
            response = await client.get(f"/workspaces/{workspace_id}/imports/new")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert 'name="account_id"' in response.text
    assert f'value="{checking_id}"' in response.text
    assert "Chase Checking" in response.text
    assert "Mortgage" not in response.text
    assert "required" in response.text


@pytest.mark.anyio
async def test_direct_import_links_the_selected_workspace_account(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                account = Account(
                    workspace_id=workspace.id,
                    name="Chase Checking",
                    account_type="checking",
                    institution_key="chase",
                    institution="Chase",
                    is_liability=False,
                )
                session.add(account)
                session.commit()
                workspace_id = workspace.id
                account_id = account.id
            response = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={
                    "retention_choice": "retain",
                    "csrf_token": token,
                    "account_id": str(account_id),
                },
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
        with factory() as session:
            job = session.scalar(select(ImportJob))
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert job is not None and job.account_id == account_id


@pytest.mark.anyio
async def test_direct_import_rejects_an_incompatible_account_before_storing_file(
    tmp_path: Path,
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace = session.scalar(select(Workspace))
                assert workspace is not None
                mortgage = Account(
                    workspace_id=workspace.id,
                    name="Mortgage",
                    account_type="mortgage",
                    institution_key="other",
                    institution="Other",
                    is_liability=True,
                )
                session.add(mortgage)
                session.commit()
                workspace_id = workspace.id
                account_id = mortgage.id
            response = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={
                    "retention_choice": "retain",
                    "csrf_token": token,
                    "account_id": str(account_id),
                },
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            job_count = session.scalar(select(func.count()).select_from(ImportJob))
            upload_count = session.scalar(select(func.count()).select_from(UploadedFile))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert "Choose a checking, savings, or credit-card account." in response.text
    assert job_count == 0
    assert upload_count == 0
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_upload_requires_csrf_before_file_or_database_mutation(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import"},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
            )
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ImportJob)) == 0
            assert session.scalar(select(func.count()).select_from(UploadedFile)) == 0
    finally:
        engine.dispose()

    assert response.status_code == 403
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_valid_upload_maps_and_previews_before_commit(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            mapping_page = await client.get(uploaded.headers["location"])
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
                follow_redirects=False,
            )
            review = await client.get(mapped.headers["location"])
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    finally:
        engine.dispose()

    assert uploaded.status_code == 303
    assert mapping_page.status_code == 200
    assert "Date" in mapping_page.text
    assert "Example Market" in mapping_page.text
    assert 'value="mdy" selected' in mapping_page.text
    assert 'value="as_is" selected' in mapping_page.text
    assert mapped.status_code == 303
    assert review.status_code == 200
    assert "-12.34" in review.text
    assert "Uncategorized" in review.text
    assert 'name="normalized_merchant_2"' in review.text
    assert 'name="category_2"' in review.text
    assert 'name="is_subscription_2"' in review.text
    assert 'name="review_token_2"' in review.text


@pytest.mark.anyio
async def test_review_labels_ai_preselection_as_a_suggestion(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    application.state.categorization_graph = build_categorization_graph(ShoppingClassifier())
    try:
        with factory() as session:
            session.add(Category(workspace_id=None, name="Shopping", kind="expense"))
            session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
                follow_redirects=False,
            )
            review = await client.get(mapped.headers["location"])
    finally:
        engine.dispose()

    assert review.status_code == 200
    assert "Shopping" in review.text
    assert "AI suggestion" in review.text


@pytest.mark.anyio
async def test_invalid_extension_creates_no_private_record(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            response = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.txt", CSV_BYTES, "text/plain")},
            )
        with factory() as session:
            count = session.scalar(select(func.count()).select_from(ImportJob))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert count == 0
    assert "Choose a CSV or PDF transaction statement" in response.text
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_mapping_errors_are_specific_and_do_not_advance_job(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            response = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Date",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "unknown",
                    "amount_sign": "unknown",
                },
            )
        with factory() as session:
            job = session.get(ImportJob, import_id)
            assert job is not None and job.status == "awaiting_mapping"
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert "Each field must use a different CSV column" in response.text
    assert "Choose a supported date format" in response.text
    assert "Choose how signed amounts should be interpreted" in response.text


@pytest.mark.anyio
async def test_invalid_review_edit_is_preserved_without_database_write(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
            )
            review = await client.get(mapped.headers["location"])
            baseline_token = review_token(review.text, 2)
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/commit",
                data={
                    "csrf_token": token,
                    "row_numbers": "2",
                    "include_2": "on",
                    "date_2": "2026-08-01",
                    "description_2": "Corrected description",
                    "amount_2": "not money",
                    "review_token_2": baseline_token,
                },
            )
        with factory() as session:
            count = session.scalar(select(func.count()).select_from(Transaction))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert 'value="Corrected description"' in response.text
    assert 'value="not money"' in response.text
    assert "Correct the highlighted rows" in response.text
    assert count == 0


@pytest.mark.anyio
async def test_import_id_is_hidden_from_another_workspace(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with (
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as owner_client,
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://testserver"
            ) as other_client,
        ):
            application.state.google_oauth.google.claims = verified_claims(
                sub="owner-import-sub", email="owner-import@example.com", name="Owner"
            )
            await complete_sign_in(owner_client)
            owner_token = await csrf_token(owner_client)
            with factory() as session:
                owner = session.scalar(select(User).where(User.google_sub == "owner-import-sub"))
                assert owner is not None
                owner_workspace_id = owner.owned_workspaces[0].id
            uploaded = await owner_client.post(
                f"/workspaces/{owner_workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": owner_token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])

            application.state.google_oauth.google.claims = verified_claims(
                sub="other-import-sub", email="other-import@example.com", name="Other"
            )
            await complete_sign_in(other_client)
            with factory() as session:
                other = session.scalar(select(User).where(User.google_sub == "other-import-sub"))
                assert other is not None
                other_workspace_id = other.owned_workspaces[0].id
            response = await other_client.get(
                f"/workspaces/{other_workspace_id}/imports/{import_id}/mapping"
            )
    finally:
        engine.dispose()

    assert response.status_code == 404


@pytest.mark.anyio
async def test_review_commit_writes_transactions_then_deletes_source(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
                household = Tag(
                    workspace_id=workspace_id,
                    name="Household Expenditure",
                )
                vehicle = Tag(workspace_id=None, name="Vehicle")
                subscription = Tag(workspace_id=None, name="Subscription")
                session.add_all((household, vehicle, subscription))
                session.commit()
                household_id = household.id
                vehicle_id = vehicle.id
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "delete_after_import", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
            )
            review = await client.get(mapped.headers["location"])
            assert 'name="tag_ids_2"' in review.text
            assert "Household Expenditure" in review.text
            assert 'name="billing_period_months_2"' in review.text
            assert 'class="import-review" data-page-size="50"' in review.text
            assert "Select matching counterparty" in review.text
            assert 'data-tag-input list="tag-options-2"' in review.text
            assert "data-create-tag" in review.text
            assert 'data-review-group="' in review.text
            assert "/static/import-review.js" in review.text
            assert "Description / status" in review.text
            assert "<th>Status</th>" not in review.text
            with factory() as session:
                category_id = session.scalar(select(Category.id))
                assert category_id is not None
            baseline_token = review_token(review.text, 2)
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/commit",
                data={
                    "csrf_token": token,
                    "row_numbers": "2",
                    "include_2": "on",
                    "date_2": "2026-08-01",
                    "description_2": "Example Market",
                    "amount_2": "-12.34",
                    "normalized_merchant_2": "Reviewed Market",
                    "category_2": str(category_id),
                    "is_subscription_2": "on",
                    "tag_ids_2": [str(household_id), str(vehicle_id)],
                    "billing_period_months_2": "6",
                    "categorization_source_2": "workspace_rule",
                    "original_normalized_merchant_2": "Reviewed Market",
                    "original_category_2": str(category_id),
                    "original_is_subscription_2": "yes",
                    "original_categorization_source_2": "workspace_rule",
                    "review_token_2": baseline_token,
                },
                follow_redirects=False,
            )
        with factory() as session:
            transaction = session.scalar(select(Transaction))
            job = session.get(ImportJob, import_id)
            uploaded_file = session.scalar(select(UploadedFile))
            assert transaction is not None
            assert transaction.amount_cents == -1234
            assert transaction.normalized_merchant == "Reviewed Market"
            assert transaction.is_subscription is True
            assert [tag.name for tag in transaction.tags] == [
                "Household Expenditure",
                "Subscription",
                "Vehicle",
            ]
            assert transaction.billing_period_months == 6
            assert transaction.categorization_source == "manual"
            assert job is not None and job.status == "committed"
            assert uploaded_file is not None and uploaded_file.deleted is True
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == (f"/workspaces/{workspace_id}/imports/{import_id}")
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_review_commit_rejects_tampered_preview_token(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            mapped = await client.post(
                uploaded.headers["location"],
                data={
                    "csrf_token": token,
                    "date_column": "Date",
                    "description_column": "Description",
                    "amount_mode": "single",
                    "amount_column": "Amount",
                    "date_format": "mdy",
                    "amount_sign": "as_is",
                },
            )
            review = await client.get(mapped.headers["location"])
            baseline_token = review_token(review.text, 2)
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/commit",
                data={
                    "csrf_token": token,
                    "row_numbers": "2",
                    "include_2": "on",
                    "date_2": "2026-08-01",
                    "description_2": "Example Market",
                    "amount_2": "-12.34",
                    "review_token_2": f"{baseline_token}tampered",
                },
            )
        with factory() as session:
            transaction_count = session.scalar(select(func.count()).select_from(Transaction))
    finally:
        engine.dispose()

    assert response.status_code == 400
    assert "Review data could not be verified" in response.text
    assert transaction_count == 0


@pytest.mark.anyio
async def test_cancel_deletes_source_and_records_canceled_state(tmp_path: Path) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])
            response = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/cancel",
                data={"csrf_token": token},
                follow_redirects=False,
            )
        with factory() as session:
            job = session.get(ImportJob, import_id)
            uploaded_file = session.scalar(select(UploadedFile))
            assert job is not None and job.status == "canceled"
            assert uploaded_file is not None and uploaded_file.deleted is True
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == f"/workspaces/{workspace_id}"
    assert list(tmp_path.rglob("*.csv")) == []


@pytest.mark.anyio
async def test_cancel_cleanup_failure_links_to_successful_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application, factory, engine = build_route_test_app(tmp_path)
    store = application.state.upload_store
    original_delete = store.delete
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            await complete_sign_in(client)
            token = await csrf_token(client)
            with factory() as session:
                workspace_id = session.scalar(select(Workspace.id))
                assert workspace_id is not None
            uploaded = await client.post(
                f"/workspaces/{workspace_id}/imports",
                data={"retention_choice": "retain", "csrf_token": token},
                files={"statement": ("synthetic.csv", CSV_BYTES, "text/csv")},
                follow_redirects=False,
            )
            import_id = int(uploaded.headers["location"].split("/")[-2])

            def fail_delete(storage_key: str) -> None:
                raise OSError("synthetic cleanup failure")

            monkeypatch.setattr(store, "delete", fail_delete)
            canceled = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/cancel",
                data={"csrf_token": token},
                follow_redirects=False,
            )
            result = await client.get(canceled.headers["location"])
            monkeypatch.setattr(store, "delete", original_delete)
            retried = await client.post(
                f"/workspaces/{workspace_id}/imports/{import_id}/cleanup",
                data={"csrf_token": token},
                follow_redirects=False,
            )
        with factory() as session:
            job = session.get(ImportJob, import_id)
            uploaded_file = session.scalar(select(UploadedFile))
            assert job is not None and job.status == "canceled"
            assert uploaded_file is not None and uploaded_file.deleted is True
    finally:
        engine.dispose()

    assert canceled.status_code == 303
    assert canceled.headers["location"].endswith(f"/imports/{import_id}")
    assert result.status_code == 200
    assert "Retry source cleanup" in result.text
    assert retried.status_code == 303
    assert list(tmp_path.rglob("*.csv")) == []
