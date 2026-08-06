"""create budgets, savings_goals, and insight_snapshots tables

Revision ID: 0004_planning_insights
Revises: 0003_payslips_income
Create Date: 2026-08-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_planning_insights"
down_revision = "0003_payslips_income"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "category_id",
            "period_month",
            name="uix_workspace_category_month",
        ),
    )
    op.create_index("ix_budgets_workspace_id", "budgets", ["workspace_id"])
    op.create_index("ix_budgets_category_id", "budgets", ["category_id"])
    op.create_index("ix_workspace_budget_period", "budgets", ["workspace_id", "period_month"])

    op.create_table(
        "savings_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("target_amount_cents", sa.Integer(), nullable=False),
        sa.Column("current_amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("monthly_contribution_cents", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_savings_goals_workspace_id", "savings_goals", ["workspace_id"])

    op.create_table(
        "insight_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("snapshot_data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_insight_snapshots_workspace_id", "insight_snapshots", ["workspace_id"])
    op.create_index(
        "ix_workspace_insight_period",
        "insight_snapshots",
        ["workspace_id", "period_start"],
    )


def downgrade():
    op.drop_table("insight_snapshots")
    op.drop_table("savings_goals")
    op.drop_table("budgets")
