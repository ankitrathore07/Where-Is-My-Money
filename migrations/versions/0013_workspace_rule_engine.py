"""migrate merchant rules to the workspace rule engine

Revision ID: 0013_workspace_rule_engine
Revises: 0012_tax_refund_and_installment_tags
Create Date: 2026-08-15 00:00:00.000000
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0013_workspace_rule_engine"
down_revision = "0012_tax_refund_and_installment_tags"
branch_labels = None
depends_on = None


def _legacy_condition(merchant_pattern: str) -> dict[str, object]:
    return {
        "field": "merchant_key",
        "operator": "exact",
        "type": "predicate",
        "value": merchant_pattern,
        "version": 1,
    }


def _legacy_rule_name(merchant_pattern: str | None, rule_id: int) -> str:
    if merchant_pattern:
        return merchant_pattern[:120]
    return f"Legacy rule {rule_id}"


def _downgrade_pattern(rule_id: int, suffix: int = 0) -> str:
    suffix_part = f"_{suffix}" if suffix else ""
    return f"__workspace_rule_{rule_id}{suffix_part}__"


def upgrade():
    op.add_column(
        "merchant_rules",
        sa.Column("name", sa.String(length=120), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "merchant_rules",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "merchant_rules",
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "merchant_rules",
        sa.Column(
            "condition_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "merchant_rules",
        sa.Column("condition_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "merchant_rules",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "select id, workspace_id, merchant_pattern from merchant_rules "
            "order by workspace_id, created_at, id"
        )
    ).mappings()
    priorities: dict[int, int] = {}
    for row in rows:
        workspace_id = row["workspace_id"]
        priority = priorities.get(workspace_id, 0)
        priorities[workspace_id] = priority + 1
        merchant_pattern = row["merchant_pattern"]
        values: dict[str, object] = {
            "name": _legacy_rule_name(merchant_pattern, row["id"]),
            "priority": priority,
        }
        if merchant_pattern:
            values.update(
                condition_version=1,
                condition_json=json.dumps(
                    _legacy_condition(merchant_pattern), separators=(",", ":"), sort_keys=True
                ),
            )
        connection.execute(
            sa.text(
                "update merchant_rules set "
                "name = :name, "
                "priority = :priority, "
                "condition_version = coalesce(:condition_version, condition_version), "
                "condition_json = coalesce(:condition_json, condition_json) "
                "where id = :id"
            ),
            {
                "id": row["id"],
                "name": values.get("name"),
                "priority": values["priority"],
                "condition_version": values.get("condition_version"),
                "condition_json": values.get("condition_json"),
            },
        )

    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.alter_column(
            "merchant_pattern",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch_op.create_check_constraint("ck_merchant_rules_priority_nonnegative", "priority >= 0")
        batch_op.create_check_constraint(
            "ck_merchant_rules_lock_version_positive", "lock_version > 0"
        )
        batch_op.create_check_constraint(
            "ck_merchant_rules_condition_version_one", "condition_version = 1"
        )
    op.create_index(
        "ix_merchant_rules_workspace_enabled_priority",
        "merchant_rules",
        ["workspace_id", "enabled", "priority"],
    )

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("merchant_rule_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_transactions_merchant_rule_id",
            "merchant_rules",
            ["merchant_rule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_transactions_merchant_rule_id", ["merchant_rule_id"])


def downgrade():
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_transactions_merchant_rule_id")
        batch_op.drop_constraint("fk_transactions_merchant_rule_id", type_="foreignkey")
        batch_op.drop_column("merchant_rule_id")

    connection = op.get_bind()
    existing_patterns = {
        (row["workspace_id"], row["merchant_pattern"])
        for row in connection.execute(
            sa.text(
                "select workspace_id, merchant_pattern from merchant_rules "
                "where merchant_pattern is not null"
            )
        ).mappings()
    }
    rows = connection.execute(
        sa.text(
            "select id, workspace_id from merchant_rules where merchant_pattern is null order by id"
        )
    ).mappings()
    for row in rows:
        suffix = 0
        merchant_pattern = _downgrade_pattern(row["id"], suffix)
        while (row["workspace_id"], merchant_pattern) in existing_patterns:
            suffix += 1
            merchant_pattern = _downgrade_pattern(row["id"], suffix)
        connection.execute(
            sa.text(
                "update merchant_rules set merchant_pattern = :merchant_pattern where id = :id"
            ),
            {"id": row["id"], "merchant_pattern": merchant_pattern},
        )
        existing_patterns.add((row["workspace_id"], merchant_pattern))

    op.drop_index("ix_merchant_rules_workspace_enabled_priority", table_name="merchant_rules")
    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.drop_constraint("ck_merchant_rules_condition_version_one", type_="check")
        batch_op.drop_constraint("ck_merchant_rules_lock_version_positive", type_="check")
        batch_op.drop_constraint("ck_merchant_rules_priority_nonnegative", type_="check")
        batch_op.alter_column(
            "merchant_pattern",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.drop_column("lock_version")
        batch_op.drop_column("condition_json")
        batch_op.drop_column("condition_version")
        batch_op.drop_column("priority")
        batch_op.drop_column("enabled")
        batch_op.drop_column("name")
