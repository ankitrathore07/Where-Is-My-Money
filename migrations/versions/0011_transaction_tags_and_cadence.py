"""add transaction tags and billing cadence

Revision ID: 0011_transaction_tags_and_cadence
Revises: 0010_provider_aware_transaction_imports
Create Date: 2026-08-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

from app.tags.catalog import BUILTIN_TAG_NAMES

revision = "0011_transaction_tags_and_cadence"
down_revision = "0010_provider_aware_transaction_imports"
branch_labels = None
depends_on = None

BILLING_PERIOD_CHECK = (
    "billing_period_months IS NULL OR (billing_period_months >= 1 AND billing_period_months <= 120)"
)


def upgrade():
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_key", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_tags_workspace_id", "tags", ["workspace_id"])
    op.create_index(
        "uix_custom_tag_name_key",
        "tags",
        ["workspace_id", "name_key"],
        unique=True,
        sqlite_where=sa.text("workspace_id IS NOT NULL"),
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.create_index(
        "uix_builtin_tag_name_key",
        "tags",
        ["name_key"],
        unique=True,
        sqlite_where=sa.text("workspace_id IS NULL"),
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_table(
        "transaction_tags",
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "merchant_rule_tags",
        sa.Column(
            "merchant_rule_id",
            sa.Integer(),
            sa.ForeignKey("merchant_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("billing_period_months", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_transactions_billing_period_months", BILLING_PERIOD_CHECK
        )
    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.add_column(sa.Column("billing_period_months", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_merchant_rules_billing_period_months", BILLING_PERIOD_CHECK
        )

    connection = op.get_bind()
    for name in BUILTIN_TAG_NAMES:
        connection.execute(
            sa.text(
                "insert into tags (workspace_id, name, name_key) values (null, :name, :name_key)"
            ),
            {"name": name, "name_key": " ".join(name.split()).casefold()},
        )
    connection.execute(
        sa.text(
            "insert into transaction_tags (transaction_id, tag_id) "
            "select transactions.id, tags.id from transactions cross join tags "
            "where transactions.is_subscription = :true_value "
            "and tags.workspace_id is null and tags.name_key = 'subscription'"
        ),
        {"true_value": True},
    )
    connection.execute(
        sa.text(
            "insert into merchant_rule_tags (merchant_rule_id, tag_id) "
            "select merchant_rules.id, tags.id from merchant_rules cross join tags "
            "where merchant_rules.is_subscription = :true_value "
            "and tags.workspace_id is null and tags.name_key = 'subscription'"
        ),
        {"true_value": True},
    )


def downgrade():
    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.drop_constraint("ck_merchant_rules_billing_period_months", type_="check")
        batch_op.drop_column("billing_period_months")
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint("ck_transactions_billing_period_months", type_="check")
        batch_op.drop_column("billing_period_months")
    op.drop_table("merchant_rule_tags")
    op.drop_table("transaction_tags")
    op.drop_index("uix_builtin_tag_name_key", table_name="tags")
    op.drop_index("uix_custom_tag_name_key", table_name="tags")
    op.drop_index("ix_tags_workspace_id", table_name="tags")
    op.drop_table("tags")
