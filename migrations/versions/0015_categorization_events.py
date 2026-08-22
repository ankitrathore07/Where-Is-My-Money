"""add redacted transaction categorization events

Revision ID: 0015_categorization_events
Revises: 0014_rule_application_runs
Create Date: 2026-08-18 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_categorization_events"
down_revision = "0014_rule_application_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transaction_categorization_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_source", sa.String(length=50), nullable=False),
        sa.Column("new_source", sa.String(length=50), nullable=False),
        sa.Column(
            "previous_rule_id",
            sa.Integer(),
            sa.ForeignKey("merchant_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "new_rule_id",
            sa.Integer(),
            sa.ForeignKey("merchant_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "previous_source IN ('manual', 'workspace_rule', 'provider_rule', "
            "'builtin_rule', 'ai_suggestion', 'uncategorized')",
            name="ck_transaction_categorization_events_previous_source",
        ),
        sa.CheckConstraint(
            "new_source IN ('manual', 'workspace_rule', 'provider_rule', "
            "'builtin_rule', 'ai_suggestion', 'uncategorized')",
            name="ck_transaction_categorization_events_new_source",
        ),
        sa.CheckConstraint(
            "reason IN ('manual_correction', 'import_commit', 'historical_application')",
            name="ck_transaction_categorization_events_reason",
        ),
        sa.CheckConstraint(
            "previous_source <> new_source OR "
            "coalesce(previous_rule_id, -1) <> coalesce(new_rule_id, -1)",
            name="ck_transaction_categorization_events_changed",
        ),
    )
    op.create_index(
        "ix_transaction_categorization_events_workspace_id",
        "transaction_categorization_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_transaction_categorization_events_transaction_id",
        "transaction_categorization_events",
        ["transaction_id"],
    )
    op.create_index(
        "ix_transaction_categorization_events_previous_rule_id",
        "transaction_categorization_events",
        ["previous_rule_id"],
    )
    op.create_index(
        "ix_transaction_categorization_events_new_rule_id",
        "transaction_categorization_events",
        ["new_rule_id"],
    )
    op.create_index(
        "ix_transaction_categorization_events_workspace_created",
        "transaction_categorization_events",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transaction_categorization_events_workspace_created",
        table_name="transaction_categorization_events",
    )
    op.drop_index(
        "ix_transaction_categorization_events_new_rule_id",
        table_name="transaction_categorization_events",
    )
    op.drop_index(
        "ix_transaction_categorization_events_previous_rule_id",
        table_name="transaction_categorization_events",
    )
    op.drop_index(
        "ix_transaction_categorization_events_transaction_id",
        table_name="transaction_categorization_events",
    )
    op.drop_index(
        "ix_transaction_categorization_events_workspace_id",
        table_name="transaction_categorization_events",
    )
    op.drop_table("transaction_categorization_events")
