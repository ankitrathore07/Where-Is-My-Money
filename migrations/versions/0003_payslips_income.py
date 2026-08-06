"""create payslips and income_records tables

Revision ID: 0003_payslips_income
Revises: 0002_imports_transactions
Create Date: 2026-08-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_payslips_income"
down_revision = "0002_imports_transactions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payslips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "uploaded_file_id", sa.Integer(), sa.ForeignKey("uploaded_files.id"), nullable=True
        ),
        sa.Column("employer", sa.String(length=255), nullable=True),
        sa.Column("pay_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pay_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_fields", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=50), nullable=False, server_default="pending"),
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
    op.create_index("ix_payslips_workspace_id", "payslips", ["workspace_id"])

    op.create_table(
        "income_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("payslip_id", sa.Integer(), sa.ForeignKey("payslips.id"), nullable=True),
        sa.Column("employer", sa.String(length=255), nullable=True),
        sa.Column("pay_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gross_pay_cents", sa.Integer(), nullable=False),
        sa.Column("net_pay_cents", sa.Integer(), nullable=False),
        sa.Column("taxes_cents", sa.Integer(), nullable=False),
        sa.Column("deductions_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_income_records_workspace_id", "income_records", ["workspace_id"])
    op.create_index("ix_income_records_payslip_id", "income_records", ["payslip_id"])


def downgrade():
    op.drop_table("income_records")
    op.drop_table("payslips")
