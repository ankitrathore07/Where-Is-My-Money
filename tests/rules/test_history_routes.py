from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous.timed import TimestampSigner
from sqlalchemy import func, select

from app.db.models import (
    Account,
    Category,
    ImportJob,
    MerchantRule,
    RuleApplicationRun,
    Tag,
    Transaction,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.rules.application_tokens import load_application_token
from app.rules.types import PredicateCondition
from app.rules.validation import condition_to_json
from tests.route_helpers import build_route_test_app, complete_sign_in, csrf_token


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def signed_in_client(application):
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        await complete_sign_in(client)
        yield client


@dataclass(frozen=True)
class HistoryScenario:
    workspace_id: int
    rule_id: int
    account_id: int
    current_category_id: int
    target_category_id: int
    target_tag_id: int
    eligible_ids: tuple[int, ...]
    unchanged_id: int | None
    manual_id: int | None
    conflict_id: int | None
    invalid_id: int | None
    foreign_transaction_id: int
    foreign_category_id: int


def history_url(workspace_id: int, rule_id: int) -> str:
    return f"/workspaces/{workspace_id}/rules/{rule_id}/apply"


def history_preview_url(workspace_id: int, rule_id: int) -> str:
    return f"{history_url(workspace_id, rule_id)}/preview"


def history_selection_url(workspace_id: int, rule_id: int) -> str:
    return f"{history_url(workspace_id, rule_id)}/confirm"


def history_confirm_url(workspace_id: int) -> str:
    return f"/workspaces/{workspace_id}/rules/history/confirm"


def _condition(field: str, operator: str, value: object) -> dict[str, object]:
    return json.loads(condition_to_json(PredicateCondition(field, operator, value)))


def _transaction(
    session,
    workspace_id: int,
    category_id: int,
    *,
    description: str,
    import_job_id: int | None,
    merchant: str = "Old merchant",
    source: str = "uncategorized",
    merchant_rule_id: int | None = None,
    tags: tuple[Tag, ...] = (),
    is_subscription: bool = False,
    cadence: int | None = None,
    fingerprint: str | None = None,
) -> Transaction:
    transaction = Transaction(
        workspace_id=workspace_id,
        date=datetime(2026, 8, 15, 12, 30, tzinfo=UTC),
        description=description,
        normalized_merchant=merchant,
        amount_cents=-1_250,
        category_id=category_id,
        categorization_source=source,
        merchant_rule_id=merchant_rule_id,
        tags=list(tags),
        is_subscription=is_subscription,
        billing_period_months=cadence,
        import_job_id=import_job_id,
        duplicate_fingerprint=fingerprint,
    )
    session.add(transaction)
    session.flush()
    return transaction


def _seed_history_scenario(
    factory,
    workspace_id: int,
    *,
    eligible_count: int = 1,
    include_classifications: bool = True,
) -> HistoryScenario:
    with factory() as session:
        current = Category(
            workspace_id=workspace_id,
            name="Current",
            name_key="current",
            kind="expense",
        )
        target = Category(
            workspace_id=workspace_id,
            name="Coffee",
            name_key="coffee",
            kind="expense",
        )
        conflict_target = Category(
            workspace_id=workspace_id,
            name="Priority winner",
            name_key="priority winner",
            kind="expense",
        )
        target_tag = Tag(
            workspace_id=workspace_id,
            name="Morning",
            name_key="morning",
        )
        account = Account(
            workspace_id=workspace_id,
            name="Household checking",
            account_type="checking",
            institution_key="other",
            institution="Local",
            is_liability=False,
        )
        session.add_all([current, target, conflict_target, target_tag, account])
        session.flush()
        import_job = ImportJob(
            workspace_id=workspace_id,
            account_id=account.id,
            status="committed",
        )
        session.add(import_job)
        session.flush()

        foreign_owner = User(
            google_sub=f"history-foreign-{workspace_id}",
            email=f"history-foreign-{workspace_id}@example.com",
            display_name="Foreign owner",
        )
        session.add(foreign_owner)
        session.flush()
        foreign_workspace = Workspace(
            name="Private history workspace",
            is_personal=True,
            owner_id=foreign_owner.id,
        )
        session.add(foreign_workspace)
        session.flush()
        foreign_category = Category(
            workspace_id=foreign_workspace.id,
            name="Private history category",
            name_key="private history category",
            kind="expense",
        )
        session.add(foreign_category)
        session.flush()

        conflict_rule = MerchantRule(
            workspace_id=workspace_id,
            merchant_pattern=None,
            name="Higher priority coffee",
            enabled=True,
            priority=0,
            condition_version=1,
            condition_json=_condition("description", "exact", "COFFEE VIP"),
            lock_version=1,
            normalized_merchant="VIP Coffee",
            category_id=conflict_target.id,
        )
        invalid_rule = MerchantRule(
            workspace_id=workspace_id,
            merchant_pattern=None,
            name="Invalid higher action",
            enabled=True,
            priority=1,
            condition_version=1,
            condition_json=_condition("description", "exact", "COFFEE INVALID"),
            lock_version=1,
            normalized_merchant="Invalid Coffee",
            category_id=foreign_category.id,
        )
        target_rule = MerchantRule(
            workspace_id=workspace_id,
            merchant_pattern=None,
            name="Coffee history",
            enabled=True,
            priority=2,
            condition_version=1,
            condition_json=_condition("description", "contains", "COFFEE"),
            lock_version=1,
            normalized_merchant="Coffee Club",
            category_id=target.id,
            tags=[target_tag],
            is_subscription=True,
            billing_period_months=1,
        )
        session.add_all([conflict_rule, invalid_rule, target_rule])
        session.flush()

        eligible = tuple(
            _transaction(
                session,
                workspace_id,
                current.id,
                description=f"COFFEE SHOP {index + 1}",
                import_job_id=import_job.id,
                fingerprint=f"history-{workspace_id}-{index + 1}",
            )
            for index in range(eligible_count)
        )
        unchanged = manual = conflict = invalid = None
        if include_classifications:
            unchanged = _transaction(
                session,
                workspace_id,
                target.id,
                description="COFFEE SAME",
                import_job_id=import_job.id,
                merchant="Coffee Club",
                source="workspace_rule",
                merchant_rule_id=target_rule.id,
                tags=(target_tag,),
                is_subscription=True,
                cadence=1,
            )
            manual = _transaction(
                session,
                workspace_id,
                current.id,
                description="COFFEE MANUAL",
                import_job_id=import_job.id,
                source="manual",
            )
            conflict = _transaction(
                session,
                workspace_id,
                current.id,
                description="COFFEE VIP",
                import_job_id=import_job.id,
            )
            invalid = _transaction(
                session,
                workspace_id,
                current.id,
                description="COFFEE INVALID",
                import_job_id=import_job.id,
            )
            _transaction(
                session,
                workspace_id,
                current.id,
                description="TEA SHOP",
                import_job_id=import_job.id,
            )
        foreign_transaction = _transaction(
            session,
            foreign_workspace.id,
            foreign_category.id,
            description="COFFEE PRIVATE",
            import_job_id=None,
            fingerprint=f"foreign-history-{workspace_id}",
        )
        session.commit()
        return HistoryScenario(
            workspace_id=workspace_id,
            rule_id=target_rule.id,
            account_id=account.id,
            current_category_id=current.id,
            target_category_id=target.id,
            target_tag_id=target_tag.id,
            eligible_ids=tuple(item.id for item in eligible),
            unchanged_id=unchanged.id if unchanged else None,
            manual_id=manual.id if manual else None,
            conflict_id=conflict.id if conflict else None,
            invalid_id=invalid.id if invalid else None,
            foreign_transaction_id=foreign_transaction.id,
            foreign_category_id=foreign_category.id,
        )


def _workspace_id(factory) -> int:
    with factory() as session:
        workspace_id = session.scalar(select(Workspace.id).order_by(Workspace.id))
        assert workspace_id is not None
        return workspace_id


def _filters(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "date_from": "",
        "date_to": "",
        "account_id": "",
        "category_id": "",
        "direction": "all",
    }
    values.update(overrides)
    return values


def _history_token(page: str) -> str:
    match = re.search(r'name="confirmation_token" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def _run_id(page: str) -> int:
    match = re.search(r"Audit run (\d+)", page)
    assert match is not None
    return int(match.group(1))


def _state(factory, transaction_id: int) -> tuple[object, ...]:
    with factory() as session:
        transaction = session.get(Transaction, transaction_id)
        assert transaction is not None
        return (
            transaction.normalized_merchant,
            transaction.category_id,
            transaction.categorization_source,
            transaction.merchant_rule_id,
            tuple(tag.id for tag in transaction.tags),
            transaction.is_subscription,
            transaction.billing_period_months,
            transaction.date,
            transaction.description,
            transaction.amount_cents,
            transaction.import_job_id,
            transaction.duplicate_fingerprint,
        )


async def _selected_preview(client, scenario: HistoryScenario):
    return await client.post(
        history_selection_url(scenario.workspace_id, scenario.rule_id),
        data={
            **_filters(),
            "transaction_ids": [str(scenario.eligible_ids[0])],
            "csrf_token": await csrf_token(client),
        },
    )


@pytest.mark.anyio
async def test_history_pages_require_authentication_and_workspace_membership(
    tmp_path: Path,
) -> None:
    """Break if historical actions bypass either authentication or membership."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as anonymous:
            anonymous_response = await anonymous.get(
                history_url(workspace_id, scenario.rule_id), follow_redirects=False
            )
        async with signed_in_client(application) as client:
            foreign_response = await client.get(
                history_url(999_999, scenario.rule_id), follow_redirects=False
            )
    finally:
        engine.dispose()

    assert anonymous_response.status_code == 303
    assert anonymous_response.headers["location"] == "/"
    assert foreign_response.status_code == 404


@pytest.mark.anyio
async def test_disabled_rule_history_get_and_preview_share_the_generic_boundary(
    tmp_path: Path,
) -> None:
    """Break if GET presents a history action that the preview service cannot perform."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            with factory() as session:
                rule = session.get(MerchantRule, scenario.rule_id)
                assert rule is not None
                rule.enabled = False
                session.commit()
            opened = await client.get(history_url(workspace_id, scenario.rule_id))
            preview = await client.post(
                history_preview_url(workspace_id, scenario.rule_id),
                data={**_filters(), "csrf_token": await csrf_token(client)},
            )
        with factory() as session:
            run_count = session.scalar(select(func.count(RuleApplicationRun.id)))
    finally:
        engine.dispose()

    assert [opened.status_code, preview.status_code] == [404, 404]
    assert run_count == 0
    assert "Coffee history" not in opened.text
    assert "Coffee history" not in preview.text


@pytest.mark.anyio
async def test_history_preview_renders_bounded_classifications_and_commits_redacted_audit(
    tmp_path: Path,
) -> None:
    """Break if the selection page hides effects/counts or loses its committed signed audit."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            opened = await client.get(history_url(workspace_id, scenario.rule_id))
            preview = await client.post(
                history_preview_url(workspace_id, scenario.rule_id),
                data={
                    **_filters(
                        date_from="2026-08-01",
                        date_to="2026-08-31",
                        account_id=str(scenario.account_id),
                        direction="expense",
                    ),
                    "csrf_token": await csrf_token(client),
                },
            )
        with factory() as session:
            run = session.scalar(select(RuleApplicationRun))
            user_id = session.scalar(select(User.id).where(User.google_sub == "import-route-sub"))
            assert run is not None
            audit = (
                run.status,
                run.initiated_by_user_id,
                run.matched_count,
                run.changed_count,
                run.unchanged_count,
                run.manual_skip_count,
                run.conflict_skip_count,
                tuple(run.selection_json["selected_transaction_ids"]),
            )
    finally:
        engine.dispose()

    assert opened.status_code == 200
    assert "Apply Coffee history to transaction history" in opened.text
    assert "Date from" in opened.text
    assert "Current category" in opened.text
    assert preview.status_code == 200
    assert "Choose history changes" in preview.text
    assert "set merchant to “Coffee Club”" in preview.text
    assert "set category to “Coffee”" in preview.text
    assert "replace tags with “Morning”" in preview.text
    assert "1</strong><span>would change" in preview.text
    assert "1</strong><span>already identical" in preview.text
    assert "1</strong><span>manual choices protected" in preview.text
    assert "1</strong><span>higher-priority conflicts" in preview.text
    assert "1</strong><span>invalid rule actions" in preview.text
    assert "0</strong><span>unavailable evaluations" in preview.text
    assert preview.text.count('name="transaction_ids"') == 1
    assert f'value="{scenario.eligible_ids[0]}" checked' in preview.text
    assert audit == ("previewed", user_id, 5, 1, 1, 1, 1, scenario.eligible_ids)
    assert "COFFEE SHOP 1" not in repr(run.selection_json)


@pytest.mark.anyio
async def test_history_confirmation_rejects_foreign_selection_and_preserves_rows(
    tmp_path: Path,
) -> None:
    """Break if an eligible local ID can smuggle a foreign transaction into a preview."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            local_before = _state(factory, scenario.eligible_ids[0])
            foreign_before = _state(factory, scenario.foreign_transaction_id)
            response = await client.post(
                history_selection_url(workspace_id, scenario.rule_id),
                data={
                    **_filters(),
                    "transaction_ids": [
                        str(scenario.eligible_ids[0]),
                        str(scenario.foreign_transaction_id),
                    ],
                    "csrf_token": await csrf_token(client),
                },
            )
        with factory() as session:
            run_count = session.scalar(select(func.count(RuleApplicationRun.id)))
        local_after = _state(factory, scenario.eligible_ids[0])
        foreign_after = _state(factory, scenario.foreign_transaction_id)
    finally:
        engine.dispose()

    assert response.status_code == 404
    assert local_after == local_before
    assert foreign_after == foreign_before
    assert run_count == 0
    assert "Private history" not in response.text


@pytest.mark.anyio
async def test_history_selection_caps_display_and_rejects_more_than_five_hundred(
    tmp_path: Path,
) -> None:
    """Break if the HTML flow can display or sign more than the 500-change boundary."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(
                factory,
                workspace_id,
                eligible_count=501,
                include_classifications=False,
            )
            preview = await client.post(
                history_preview_url(workspace_id, scenario.rule_id),
                data={**_filters(), "csrf_token": await csrf_token(client)},
            )
            rejected = await client.post(
                history_selection_url(workspace_id, scenario.rule_id),
                data={
                    **_filters(),
                    "transaction_ids": [str(value) for value in scenario.eligible_ids],
                    "csrf_token": await csrf_token(client),
                },
            )
        with factory() as session:
            run_count = session.scalar(select(func.count(RuleApplicationRun.id)))
            changed_sources = session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.workspace_id == workspace_id,
                    Transaction.categorization_source == "workspace_rule",
                )
            )
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert "501</strong><span>would change" in preview.text
    assert preview.text.count('name="transaction_ids"') == 500
    assert "Only the first 500 eligible changes are shown" in preview.text
    assert rejected.status_code == 422
    assert "Choose between 1 and 500 eligible transactions." in rejected.text
    assert run_count == 1
    assert changed_sources == 0


@pytest.mark.anyio
async def test_signed_confirmation_applies_explicit_effects_and_retries_truthfully(
    tmp_path: Path,
) -> None:
    """Break if selection is unsigned, action fields are partial, or retry invents a result."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            transaction_id = scenario.eligible_ids[0]
            before = _state(factory, transaction_id)
            previewed = await client.post(
                history_preview_url(workspace_id, scenario.rule_id),
                data={**_filters(), "csrf_token": await csrf_token(client)},
            )
            selected = await _selected_preview(client, scenario)
            token = _history_token(selected.text)
            selected_run_id = _run_id(selected.text)
            payload = load_application_token(application.state.settings.secret_key, token)
            first = await client.post(
                history_confirm_url(workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token,
                },
            )
            second = await client.post(
                history_confirm_url(workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token,
                },
            )
        after = _state(factory, transaction_id)
        with factory() as session:
            confirmed_runs = tuple(
                session.scalars(
                    select(RuleApplicationRun).where(RuleApplicationRun.status == "confirmed")
                )
            )
            user_id = session.scalar(select(User.id).where(User.google_sub == "import-route-sub"))
            confirmed = tuple(
                (run.id, run.initiated_by_user_id, run.changed_count) for run in confirmed_runs
            )
    finally:
        engine.dispose()

    assert [previewed.status_code, selected.status_code] == [200, 200]
    assert "Confirm historical changes" in selected.text
    assert "date, description, amount, import job, and duplicate fingerprint stay unchanged" in (
        selected.text
    )
    assert payload.workspace_id == workspace_id
    assert payload.merchant_rule_id == scenario.rule_id
    assert payload.selected_transaction_ids == (transaction_id,)
    assert first.status_code == 200
    assert second.status_code == 200
    assert "History application complete" in first.text
    assert "1</strong><span>transaction changed" in first.text
    assert selected_run_id == confirmed[0][0]
    assert _run_id(first.text) == _run_id(second.text) == confirmed[0][0]
    assert len(confirmed) == 1
    assert confirmed[0][1:] == (user_id, 1)
    assert after[:7] == (
        "Coffee Club",
        scenario.target_category_id,
        "workspace_rule",
        scenario.rule_id,
        (scenario.target_tag_id,),
        True,
        1,
    )
    assert after[7:] == before[7:]


@pytest.mark.anyio
async def test_history_staleness_commits_flushed_audit_before_generic_conflict(
    tmp_path: Path,
) -> None:
    """Break if the route rolls back the service's intentionally flushed stale audit."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            selected = await _selected_preview(client, scenario)
            token = _history_token(selected.text)
            run_id = _run_id(selected.text)
            with factory() as session:
                transaction = session.get(Transaction, scenario.eligible_ids[0])
                assert transaction is not None
                transaction.normalized_merchant = "Concurrent merchant"
                session.commit()
            stale = await client.post(
                history_confirm_url(workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token,
                },
            )
        with factory() as session:
            run = session.get(RuleApplicationRun, run_id)
            transaction = session.get(Transaction, scenario.eligible_ids[0])
            assert run is not None
            assert transaction is not None
            persisted = (run.status, transaction.normalized_merchant, transaction.category_id)
    finally:
        engine.dispose()

    assert stale.status_code == 409
    assert stale.json() == {"detail": "The rule or preview changed. Reload and try again."}
    assert persisted == ("stale", "Concurrent merchant", scenario.current_category_id)


@pytest.mark.anyio
async def test_history_confirmation_rejects_tamper_expiry_and_wrong_workspace_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break if untrusted or expired signed state reaches transaction mutation."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            before = _state(factory, scenario.eligible_ids[0])
            selected = await _selected_preview(client, scenario)
            token = _history_token(selected.text)
            run_id = _run_id(selected.text)
            tampered = await client.post(
                history_confirm_url(workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token + "tampered",
                },
            )

            with factory() as session:
                user_id = session.scalar(
                    select(User.id).where(User.google_sub == "import-route-sub")
                )
                assert user_id is not None
                second_workspace = Workspace(
                    name="Second authorized workspace",
                    is_personal=False,
                    owner_id=user_id,
                )
                session.add(second_workspace)
                session.flush()
                session.add(
                    WorkspaceMembership(
                        workspace_id=second_workspace.id,
                        user_id=user_id,
                        role="member",
                    )
                )
                session.commit()
                second_workspace_id = second_workspace.id
            wrong_workspace = await client.post(
                history_confirm_url(second_workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token,
                },
            )

            current_timestamp = TimestampSigner.get_timestamp
            monkeypatch.setattr(
                TimestampSigner,
                "get_timestamp",
                lambda signer: current_timestamp(signer) + 3_601,
            )
            expired = await client.post(
                history_confirm_url(workspace_id),
                data={
                    "csrf_token": await csrf_token(client),
                    "confirmation_token": token,
                },
            )
        with factory() as session:
            run = session.get(RuleApplicationRun, run_id)
            assert run is not None
            run_status = run.status
        after = _state(factory, scenario.eligible_ids[0])
    finally:
        engine.dispose()

    assert tampered.status_code == 409
    assert wrong_workspace.status_code == 409
    assert expired.status_code == 409
    assert run_status == "previewed"
    assert after == before


@pytest.mark.anyio
async def test_every_history_post_requires_csrf_before_audit_or_transaction_work(
    tmp_path: Path,
) -> None:
    """Break if any stage of the historical write flow lacks CSRF enforcement."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = _workspace_id(factory)
            scenario = _seed_history_scenario(factory, workspace_id)
            before = _state(factory, scenario.eligible_ids[0])
            preview = await client.post(
                history_preview_url(workspace_id, scenario.rule_id),
                data=_filters(),
            )
            selection = await client.post(
                history_selection_url(workspace_id, scenario.rule_id),
                data={
                    **_filters(),
                    "transaction_ids": str(scenario.eligible_ids[0]),
                },
            )
            confirmation = await client.post(
                history_confirm_url(workspace_id),
                data={"confirmation_token": "not-a-token"},
            )
        with factory() as session:
            run_count = session.scalar(select(func.count(RuleApplicationRun.id)))
        after = _state(factory, scenario.eligible_ids[0])
    finally:
        engine.dispose()

    assert [preview.status_code, selection.status_code, confirmation.status_code] == [403] * 3
    assert run_count == 0
    assert after == before
