import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous.timed import TimestampSigner
from sqlalchemy import select

from app.db.models import Category, MerchantRule, Tag, Transaction, User, Workspace
from app.rules.service import RuleDraft, create_rule
from app.rules.types import AllCondition, AnyCondition, NotCondition, PredicateCondition
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


def workspace_id_for(factory) -> int:
    with factory() as session:
        workspace_id = session.scalar(select(Workspace.id))
        assert workspace_id is not None
        return workspace_id


def _seed_rule_choices(factory, workspace_id: int, *, transactions: int = 0) -> int:
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
        session.add_all([current, target])
        session.flush()
        session.add_all(
            Transaction(
                workspace_id=workspace_id,
                date=datetime(2026, 8, 15, tzinfo=UTC),
                description=f"COFFEE SHOP {index}",
                normalized_merchant="Old merchant",
                amount_cents=-500,
                category_id=current.id,
                categorization_source="uncategorized",
                is_subscription=False,
            )
            for index in range(transactions)
        )
        session.commit()
        return target.id


def valid_rule_form(*, csrf: str, category_id: int) -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "name": "Coffee shops",
        "group_mode": "all",
        "condition_field_0": "description",
        "condition_operator_0": "contains",
        "condition_text_value_0": " COFFEE ",
        "normalized_merchant": "Coffee Shop",
        "category_id": str(category_id),
        "is_subscription": "false",
        "billing_period_months": "",
    }


def confirmation_token(page: str) -> str:
    match = re.search(r'name="confirmation_token" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def confirmation_form(page: str, *, csrf: str) -> dict[str, str]:
    return {"csrf_token": csrf, "confirmation_token": confirmation_token(page)}


def _seed_rule(factory, workspace_id: int, category_id: int, *, name: str = "Coffee") -> int:
    with factory() as session:
        rule = create_rule(
            session,
            workspace_id,
            RuleDraft(
                name=name,
                condition=PredicateCondition("description", "contains", "COFFEE"),
                normalized_merchant="Coffee",
                category_id=category_id,
            ),
        )
        session.commit()
        return rule.id


@pytest.mark.anyio
async def test_rule_pages_require_authentication_and_workspace_membership(tmp_path: Path) -> None:
    """Break if rule data is reachable without both session and workspace authorization."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as anonymous:
            response = await anonymous.get("/workspaces/1/rules", follow_redirects=False)
        async with signed_in_client(application) as client:
            foreign = await client.get("/workspaces/999999/rules")
    finally:
        engine.dispose()

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert foreign.status_code == 404


@pytest.mark.anyio
async def test_rule_create_requires_preview_then_signed_confirmation(tmp_path: Path) -> None:
    """Break if a draft writes before preview or confirmation trusts editable browser values."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id, transactions=2)
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/preview",
                data=valid_rule_form(csrf=await csrf_token(client), category_id=category_id),
            )
            with factory() as session:
                assert session.scalar(select(MerchantRule.id)) is None
            saved = await client.post(
                f"/workspaces/{workspace_id}/rules",
                data={
                    **confirmation_form(preview.text, csrf=await csrf_token(client)),
                    "name": "Tampered browser name",
                },
                follow_redirects=False,
            )
        with factory() as session:
            rule = session.scalar(select(MerchantRule))
            assert rule is not None
            persisted = (rule.name, rule.condition_json, rule.priority)
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert "2 transactions would change" in preview.text
    assert saved.status_code == 303
    assert saved.headers["location"] == f"/workspaces/{workspace_id}/rules"
    assert persisted == (
        "Coffee shops",
        {
            "field": "description",
            "operator": "contains",
            "type": "predicate",
            "value": "COFFEE",
            "version": 1,
        },
        0,
    )


@pytest.mark.anyio
async def test_preview_and_confirmation_use_the_same_service_normalized_draft(
    tmp_path: Path,
) -> None:
    """Break if preview signs raw browser text but persistence later saves different values."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            form["name"] = "  Ｃｏｆｆｅｅ\u3000  shops  "
            form["normalized_merchant"] = "  Ｃｏｆｆｅｅ\u3000  Club  "
            preview = await client.post(f"/workspaces/{workspace_id}/rules/preview", data=form)
            saved = await client.post(
                f"/workspaces/{workspace_id}/rules",
                data=confirmation_form(preview.text, csrf=await csrf_token(client)),
                follow_redirects=False,
            )
        with factory() as session:
            rule = session.scalar(select(MerchantRule))
            assert rule is not None
            persisted = (rule.name, rule.normalized_merchant)
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert "<h2>Coffee shops</h2>" in preview.text
    assert "set merchant to “Coffee Club”" in preview.text
    assert saved.status_code == 303
    assert persisted == ("Coffee shops", "Coffee Club")


@pytest.mark.anyio
async def test_confirmation_rejects_tampering_and_edit_staleness(tmp_path: Path) -> None:
    """Break if a modified token or obsolete lock version can mutate a saved rule."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            form["name"] = "Edited coffee"
            form["lock_version"] = "1"
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/preview", data=form
            )
            token = confirmation_token(preview.text)
            tampered = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}",
                data={"csrf_token": await csrf_token(client), "confirmation_token": token + "x"},
            )
            with factory() as session:
                concurrent = session.get(MerchantRule, rule_id)
                assert concurrent is not None
                concurrent.name = "Concurrent name"
                concurrent.lock_version += 1
                session.commit()
            stale = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}",
                data={"csrf_token": await csrf_token(client), "confirmation_token": token},
            )
        with factory() as session:
            rule = session.get(MerchantRule, rule_id)
            assert rule is not None
            persisted = (rule.name, rule.lock_version)
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert tampered.status_code == 409
    assert stale.status_code == 409
    assert persisted == ("Concurrent name", 2)


@pytest.mark.anyio
async def test_confirmation_rejects_expired_signed_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break if a signed preview can be confirmed after its one-hour lifetime."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/preview",
                data=valid_rule_form(csrf=await csrf_token(client), category_id=category_id),
            )
            current_timestamp = TimestampSigner.get_timestamp
            monkeypatch.setattr(
                TimestampSigner,
                "get_timestamp",
                lambda signer: current_timestamp(signer) + 3_601,
            )
            expired = await client.post(
                f"/workspaces/{workspace_id}/rules",
                data=confirmation_form(preview.text, csrf=await csrf_token(client)),
            )
        with factory() as session:
            persisted_rule_id = session.scalar(select(MerchantRule.id))
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert expired.status_code == 409
    assert persisted_rule_id is None


@pytest.mark.anyio
async def test_edit_preview_confirmation_replaces_typed_values_and_increments_lock(
    tmp_path: Path,
) -> None:
    """Break if the positive edit path loses typed condition values or optimistic locking."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            edit = await client.get(f"/workspaces/{workspace_id}/rules/{rule_id}/edit")
            form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            form.update(
                {
                    "name": "Larger expenses",
                    "condition_field_0": "amount_cents",
                    "condition_operator_0": "less_than",
                    "condition_amount_value_0": "-10.50",
                    "lock_version": "1",
                }
            )
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/preview", data=form
            )
            saved = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}",
                data=confirmation_form(preview.text, csrf=await csrf_token(client)),
                follow_redirects=False,
            )
        with factory() as session:
            rule = session.get(MerchantRule, rule_id)
            assert rule is not None
            state = (rule.name, rule.condition_json, rule.lock_version)
    finally:
        engine.dispose()

    assert edit.status_code == 200
    assert 'value="Coffee"' in edit.text
    assert preview.status_code == 200
    assert saved.status_code == 303
    assert state == (
        "Larger expenses",
        {
            "field": "amount_cents",
            "operator": "less_than",
            "type": "predicate",
            "value": -1050,
            "version": 1,
        },
        2,
    )


@pytest.mark.anyio
async def test_disabled_rule_edit_preview_defers_impact_until_enabled_and_is_read_only(
    tmp_path: Path,
) -> None:
    """Break if editing a disabled rule claims it will immediately change transactions."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id, transactions=2)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            with factory() as session:
                rule = session.get(MerchantRule, rule_id)
                assert rule is not None
                rule.enabled = False
                session.commit()
            form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            form.update({"name": "Disabled coffee", "lock_version": "1"})
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/preview", data=form
            )
            with factory() as session:
                rule = session.get(MerchantRule, rule_id)
                assert rule is not None
                preview_state = (rule.name, rule.enabled, rule.lock_version)
            confirmed = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}",
                data=confirmation_form(preview.text, csrf=await csrf_token(client)),
                follow_redirects=False,
            )
        with factory() as session:
            rule = session.get(MerchantRule, rule_id)
            assert rule is not None
            transaction_merchants = tuple(
                session.scalars(select(Transaction.normalized_merchant).order_by(Transaction.id))
            )
            state = (rule.name, rule.enabled, rule.lock_version, transaction_merchants)
    finally:
        engine.dispose()

    assert preview.status_code == 200
    assert "This rule is disabled" in preview.text
    assert "No transactions will change now" in preview.text
    assert "2 transactions would change once enabled" in preview.text
    assert preview_state == ("Coffee", False, 1)
    assert confirmed.status_code == 303
    assert state == ("Disabled coffee", False, 2, ("Old merchant", "Old merchant"))


@pytest.mark.anyio
async def test_every_rule_mutation_requires_csrf(tmp_path: Path) -> None:
    """Break if preview or a lifecycle action can mutate through a cross-site POST."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            create_form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            create_preview = await client.post(
                f"/workspaces/{workspace_id}/rules/preview", data=create_form
            )
            edit_form = dict(create_form)
            edit_form["lock_version"] = "1"
            edit_preview = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/preview", data=edit_form
            )
            edit_form_without_csrf = {
                key: value for key, value in edit_form.items() if key != "csrf_token"
            }
            routes = [
                (f"/workspaces/{workspace_id}/rules/preview", {}),
                (
                    f"/workspaces/{workspace_id}/rules",
                    {"confirmation_token": confirmation_token(create_preview.text)},
                ),
                (
                    f"/workspaces/{workspace_id}/rules/{rule_id}/preview",
                    edit_form_without_csrf,
                ),
                (
                    f"/workspaces/{workspace_id}/rules/{rule_id}",
                    {"confirmation_token": confirmation_token(edit_preview.text)},
                ),
                (f"/workspaces/{workspace_id}/rules/{rule_id}/duplicate", {}),
                (f"/workspaces/{workspace_id}/rules/{rule_id}/move", {"new_index": "0"}),
                (f"/workspaces/{workspace_id}/rules/{rule_id}/enabled", {"enabled": "false"}),
                (f"/workspaces/{workspace_id}/rules/{rule_id}/delete", {"lock_version": "1"}),
                (
                    f"/workspaces/{workspace_id}/rules/simulate",
                    {
                        "description": "COFFEE",
                        "amount": "-5.00",
                        "transaction_date": "2026-08-15",
                    },
                ),
            ]
            responses = [await client.post(path, data=data) for path, data in routes]
        with factory() as session:
            rules = session.scalars(select(MerchantRule)).all()
            state = [(rule.name, rule.enabled, rule.priority) for rule in rules]
    finally:
        engine.dispose()

    assert [response.status_code for response in responses] == [403] * 9
    assert state == [("Coffee", True, 0)]


@pytest.mark.anyio
async def test_reorder_duplicate_and_enable_lifecycle_use_service_ordering(tmp_path: Path) -> None:
    """Break if lifecycle forms bypass deterministic service ordering or disabled state."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            first_id = _seed_rule(factory, workspace_id, category_id, name="First")
            second_id = _seed_rule(factory, workspace_id, category_id, name="Second")
            duplicated = await client.post(
                f"/workspaces/{workspace_id}/rules/{first_id}/duplicate",
                data={"csrf_token": await csrf_token(client)},
                follow_redirects=False,
            )
            moved = await client.post(
                f"/workspaces/{workspace_id}/rules/{second_id}/move",
                data={
                    "csrf_token": await csrf_token(client),
                    "new_index": "0",
                    "lock_version": "1",
                },
                follow_redirects=False,
            )
            disabled = await client.post(
                f"/workspaces/{workspace_id}/rules/{first_id}/enabled",
                data={
                    "csrf_token": await csrf_token(client),
                    "enabled": "false",
                    "lock_version": "1",
                },
                follow_redirects=False,
            )
            index = await client.get(f"/workspaces/{workspace_id}/rules")
        with factory() as session:
            rules = session.scalars(
                select(MerchantRule).order_by(MerchantRule.priority, MerchantRule.id)
            ).all()
            state = [(rule.name, rule.enabled, rule.priority) for rule in rules]
    finally:
        engine.dispose()

    assert [duplicated.status_code, moved.status_code, disabled.status_code] == [303, 303, 303]
    assert state == [("Second", True, 0), ("First", False, 1), ("First copy", True, 2)]
    assert "Disabled" in index.text
    assert "First copy" in index.text


@pytest.mark.anyio
async def test_delete_requires_review_and_preserves_historical_action_values(
    tmp_path: Path,
) -> None:
    """Break if deletion skips its warning or destroys the transaction's saved action values."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            with factory() as session:
                transaction = Transaction(
                    workspace_id=workspace_id,
                    date=datetime(2026, 8, 15, tzinfo=UTC),
                    description="COFFEE",
                    normalized_merchant="Coffee",
                    amount_cents=-500,
                    category_id=category_id,
                    merchant_rule_id=rule_id,
                    categorization_source="workspace_rule",
                    is_subscription=False,
                )
                session.add(transaction)
                session.commit()
                transaction_id = transaction.id
            review = await client.get(f"/workspaces/{workspace_id}/rules/{rule_id}/delete")
            deleted = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/delete",
                data={"csrf_token": await csrf_token(client), "lock_version": "1"},
                follow_redirects=False,
            )
        with factory() as session:
            transaction = session.get(Transaction, transaction_id)
            assert transaction is not None
            state = (
                transaction.merchant_rule_id,
                transaction.normalized_merchant,
                transaction.category_id,
                transaction.categorization_source,
            )
    finally:
        engine.dispose()

    assert review.status_code == 200
    assert "1 linked transaction" in review.text
    assert "1 transaction would fall through to provider or built-in categorization" in review.text
    assert deleted.status_code == 303
    assert state == (None, "Coffee", category_id, "workspace_rule")


@pytest.mark.anyio
async def test_rule_form_is_semantic_without_javascript_and_simulator_is_read_only(
    tmp_path: Path,
) -> None:
    """Break if core building depends on JavaScript or simulation writes rule state."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            _seed_rule(factory, workspace_id, category_id)
            form = await client.get(f"/workspaces/{workspace_id}/rules/new")
            simulated = await client.post(
                f"/workspaces/{workspace_id}/rules/simulate",
                data={
                    "csrf_token": await csrf_token(client),
                    "description": "COFFEE SHOP",
                    "amount": "-5.00",
                    "transaction_date": "2026-08-15",
                    "account_id": "",
                    "provider_key": "",
                },
            )
        with factory() as session:
            rule = session.scalar(select(MerchantRule))
            assert rule is not None
            state = (rule.name, rule.lock_version, rule.priority)
    finally:
        engine.dispose()

    assert form.status_code == 200
    assert '<form action="/workspaces/' in form.text
    assert 'name="builder_action" value="add_row"' in form.text
    assert 'name="condition_field_0"' in form.text
    assert 'name="condition_text_value_0"' in form.text
    assert 'name="condition_amount_value_0"' in form.text
    assert simulated.status_code == 200
    assert "Coffee" in simulated.text
    assert "Winning workspace rule" in simulated.text
    assert ">Test</a>" in simulated.text
    assert "set merchant to “Coffee”" in simulated.text
    assert "set category to “Coffee”" in simulated.text
    assert "description contains “COFFEE”: match" in simulated.text
    assert state == ("Coffee", 1, 0)


@pytest.mark.anyio
async def test_nested_saved_condition_can_edit_actions_without_flattening_the_tree(
    tmp_path: Path,
) -> None:
    """Break if the visual builder destroys a valid nested condition it cannot represent."""
    application, factory, engine = build_route_test_app(tmp_path)
    nested = AllCondition(
        (
            AnyCondition(
                (
                    PredicateCondition("description", "contains", "COFFEE"),
                    PredicateCondition("description", "contains", "TEA"),
                )
            ),
            NotCondition(PredicateCondition("direction", "equal", "income")),
        )
    )
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            with factory() as session:
                rule = create_rule(
                    session,
                    workspace_id,
                    RuleDraft(
                        name="Nested drinks",
                        condition=nested,
                        normalized_merchant="Drinks",
                        category_id=category_id,
                    ),
                )
                session.commit()
                rule_id = rule.id
                original_condition = rule.condition_json
            edit = await client.get(f"/workspaces/{workspace_id}/rules/{rule_id}/edit")
            form = valid_rule_form(csrf=await csrf_token(client), category_id=category_id)
            form.update(
                {
                    "name": "Nested drinks updated",
                    "lock_version": "1",
                    "preserve_condition": "true",
                    "condition_text_value_0": "MALICIOUS REPLACEMENT",
                }
            )
            preview = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/preview", data=form
            )
            saved = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}",
                data=confirmation_form(preview.text, csrf=await csrf_token(client)),
                follow_redirects=False,
            )
        with factory() as session:
            rule = session.get(MerchantRule, rule_id)
            assert rule is not None
            state = (rule.name, rule.condition_json, rule.lock_version)
    finally:
        engine.dispose()

    assert edit.status_code == 200
    assert "This condition uses nested groups" in edit.text
    assert preview.status_code == 200
    assert saved.status_code == 303
    assert state == ("Nested drinks updated", original_condition, 2)


@pytest.mark.anyio
@pytest.mark.parametrize("breakage", ["missing_category", "malformed_condition"])
async def test_edit_repair_state_rule_returns_safe_repair_form(
    tmp_path: Path, breakage: str
) -> None:
    """Break if the list's Edit link turns an already-invalid saved rule into a 500."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id, name="Needs repair")
            with factory() as session:
                rule = session.get(MerchantRule, rule_id)
                assert rule is not None
                if breakage == "missing_category":
                    rule.category_id = None
                else:
                    rule.condition_json = {"version": 1, "type": "all", "children": []}
                session.commit()
            response = await client.get(f"/workspaces/{workspace_id}/rules/{rule_id}/edit")
    finally:
        engine.dispose()

    assert response.status_code == 422
    assert "This saved rule needs repair" in response.text
    assert 'value="Needs repair"' in response.text
    assert 'name="condition_field_0"' in response.text


@pytest.mark.anyio
async def test_duplicate_with_inaccessible_saved_resource_returns_generic_not_found(
    tmp_path: Path,
) -> None:
    """Break if duplicate reveals that a saved action points at another workspace resource."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            with factory() as session:
                owner = User(
                    google_sub="foreign-duplicate-owner",
                    email="foreign-duplicate@example.com",
                    display_name="Foreign duplicate",
                )
                session.add(owner)
                session.flush()
                foreign_workspace = Workspace(
                    name="Foreign duplicate", is_personal=True, owner_id=owner.id
                )
                session.add(foreign_workspace)
                session.flush()
                foreign_category = Category(
                    workspace_id=foreign_workspace.id,
                    name="Private destination",
                    name_key="private destination",
                    kind="expense",
                )
                session.add(foreign_category)
                session.flush()
                rule = session.get(MerchantRule, rule_id)
                assert rule is not None
                rule.category_id = foreign_category.id
                session.commit()
            response = await client.post(
                f"/workspaces/{workspace_id}/rules/{rule_id}/duplicate",
                data={"csrf_token": await csrf_token(client)},
            )
    finally:
        engine.dispose()

    assert response.status_code == 404
    assert "Private destination" not in response.text


@pytest.mark.anyio
async def test_list_maps_inaccessible_saved_tags_to_fixed_repair_state(tmp_path: Path) -> None:
    """Break if list presentation reads foreign tag names from a corrupt association."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            rule_id = _seed_rule(factory, workspace_id, category_id)
            with factory() as session:
                owner = User(
                    google_sub="foreign-tag-owner",
                    email="foreign-tag@example.com",
                    display_name="Foreign tag owner",
                )
                session.add(owner)
                session.flush()
                foreign_workspace = Workspace(
                    name="Foreign tags", is_personal=True, owner_id=owner.id
                )
                session.add(foreign_workspace)
                session.flush()
                foreign_tag = Tag(
                    workspace_id=foreign_workspace.id,
                    name="PRIVATE PAYROLL TAG",
                    name_key="private payroll tag",
                )
                session.add(foreign_tag)
                session.flush()
                rule = session.get(MerchantRule, rule_id)
                assert rule is not None
                rule.tags.append(foreign_tag)
                session.commit()
                foreign_tag_id = foreign_tag.id
            response = await client.get(f"/workspaces/{workspace_id}/rules")
        with factory() as session:
            rule = session.get(MerchantRule, rule_id)
            assert rule is not None
            persisted_tag_ids = tuple(tag.id for tag in rule.tags)
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert "PRIVATE PAYROLL TAG" not in response.text
    assert "Needs repair" in response.text
    assert "Action details are unavailable until the rule is repaired" in response.text
    assert persisted_tag_ids == (foreign_tag_id,)


@pytest.mark.anyio
async def test_list_warns_only_for_an_identical_earlier_enabled_condition(tmp_path: Path) -> None:
    """Break if ordinary rule ordering is mislabeled as a detected conflict."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            category_id = _seed_rule_choices(factory, workspace_id)
            first_id = _seed_rule(factory, workspace_id, category_id, name="Coffee")
            with factory() as session:
                create_rule(
                    session,
                    workspace_id,
                    RuleDraft(
                        name="Tea",
                        condition=PredicateCondition("description", "contains", "TEA"),
                        normalized_merchant="Tea",
                        category_id=category_id,
                    ),
                )
                session.commit()
            unrelated = await client.get(f"/workspaces/{workspace_id}/rules")
            await client.post(
                f"/workspaces/{workspace_id}/rules/{first_id}/duplicate",
                data={"csrf_token": await csrf_token(client)},
            )
            duplicated = await client.get(f"/workspaces/{workspace_id}/rules")
    finally:
        engine.dispose()

    assert "Detected identical condition" not in unrelated.text
    assert "may win" not in unrelated.text
    assert "Detected identical condition" in duplicated.text


@pytest.mark.anyio
async def test_foreign_rule_ids_return_not_found_for_every_lifecycle_route(tmp_path: Path) -> None:
    """Break if a guessed foreign rule ID leaks or mutates a different workspace."""
    application, factory, engine = build_route_test_app(tmp_path)
    try:
        async with signed_in_client(application) as client:
            workspace_id = workspace_id_for(factory)
            _seed_rule_choices(factory, workspace_id)
            with factory() as session:
                owner = User(
                    google_sub="foreign-rules-owner",
                    email="foreign-rules@example.com",
                    display_name="Foreign",
                )
                session.add(owner)
                session.flush()
                foreign_workspace = Workspace(name="Foreign", is_personal=True, owner_id=owner.id)
                session.add(foreign_workspace)
                session.flush()
                foreign_category = Category(
                    workspace_id=foreign_workspace.id,
                    name="Secret",
                    name_key="secret",
                    kind="expense",
                )
                session.add(foreign_category)
                session.flush()
                foreign_rule = create_rule(
                    session,
                    foreign_workspace.id,
                    RuleDraft(
                        name="Secret foreign rule",
                        condition=PredicateCondition("description", "contains", "SECRET"),
                        normalized_merchant=None,
                        category_id=foreign_category.id,
                    ),
                )
                session.commit()
                foreign_rule_id = foreign_rule.id
            token = await csrf_token(client)
            responses = [
                await client.get(f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/edit"),
                await client.get(f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/delete"),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/duplicate",
                    data={"csrf_token": token},
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/preview",
                    data={"csrf_token": token},
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}",
                    data={"csrf_token": token, "confirmation_token": "not-a-token"},
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/move",
                    data={"csrf_token": token},
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/enabled",
                    data={"csrf_token": token},
                ),
                await client.post(
                    f"/workspaces/{workspace_id}/rules/{foreign_rule_id}/delete",
                    data={"csrf_token": token},
                ),
            ]
        with factory() as session:
            foreign = session.get(MerchantRule, foreign_rule_id)
            assert foreign is not None
            foreign_name = foreign.name
    finally:
        engine.dispose()

    assert [response.status_code for response in responses] == [404] * 8
    assert all("Secret foreign rule" not in response.text for response in responses)
    assert foreign_name == "Secret foreign rule"
