"""add redacted historical rule application audit

Revision ID: 0014_rule_application_runs
Revises: 0013_workspace_rule_engine
Create Date: 2026-08-15 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_rule_application_runs"
down_revision = "0013_workspace_rule_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_application_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_rule_id",
            sa.Integer(),
            sa.ForeignKey("merchant_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "initiated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("rule_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column("rule_lock_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("selection_json", sa.JSON(), nullable=False),
        sa.Column("preview_digest", sa.String(length=64), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unchanged_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manual_skip_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("conflict_skip_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "rule_lock_version > 0",
            name="ck_rule_application_runs_rule_lock_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('previewed', 'confirmed', 'stale', 'failed')",
            name="ck_rule_application_runs_status",
        ),
        sa.CheckConstraint(
            "matched_count >= 0 AND changed_count >= 0 AND unchanged_count >= 0 "
            "AND manual_skip_count >= 0 AND conflict_skip_count >= 0",
            name="ck_rule_application_runs_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'confirmed' AND confirmed_at IS NOT NULL) OR "
            "(status <> 'confirmed' AND confirmed_at IS NULL)",
            name="ck_rule_application_runs_confirmation_state",
        ),
    )
    op.create_index(
        "ix_rule_application_runs_workspace_id",
        "rule_application_runs",
        ["workspace_id"],
    )
    op.create_index(
        "ix_rule_application_runs_merchant_rule_id",
        "rule_application_runs",
        ["merchant_rule_id"],
    )
    op.create_index(
        "ix_rule_application_runs_initiated_by_user_id",
        "rule_application_runs",
        ["initiated_by_user_id"],
    )
    op.create_index(
        "ix_rule_application_runs_preview_digest",
        "rule_application_runs",
        ["preview_digest"],
    )


def downgrade() -> None:
    op.drop_index("ix_rule_application_runs_preview_digest", table_name="rule_application_runs")
    op.drop_index(
        "ix_rule_application_runs_initiated_by_user_id", table_name="rule_application_runs"
    )
    op.drop_index("ix_rule_application_runs_merchant_rule_id", table_name="rule_application_runs")
    op.drop_index("ix_rule_application_runs_workspace_id", table_name="rule_application_runs")
    op.drop_table("rule_application_runs")
