from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import MerchantRule, Transaction, Workspace


def test_workspace_rule_round_trips_typed_fields_and_transaction_attribution(
    session: Session, workspace: Workspace
) -> None:
    rule = MerchantRule(
        workspace_id=workspace.id,
        name="Netflix",
        merchant_pattern="NETFLIX COM",
        enabled=True,
        priority=0,
        condition_version=1,
        condition_json={
            "field": "merchant_key",
            "operator": "exact",
            "type": "predicate",
            "value": "NETFLIX COM",
            "version": 1,
        },
        lock_version=1,
    )
    transaction = Transaction(
        workspace_id=workspace.id,
        date=datetime(2026, 8, 15, tzinfo=UTC),
        description="NETFLIX COM",
        amount_cents=-1_549,
        merchant_rule=rule,
    )

    session.add(transaction)
    session.commit()

    assert transaction.merchant_rule is rule
    assert rule.transactions == [transaction]
    assert transaction.merchant_rule_id == rule.id
    assert rule.condition_json["version"] == 1
