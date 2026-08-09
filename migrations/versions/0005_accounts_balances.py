"""create accounts and account balance snapshots

Revision ID: 0005_accounts_balances
Revises: 0004_planning_insights
Create Date: 2026-08-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_accounts_balances"
down_revision = "0004_planning_insights"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=50), nullable=False),
        sa.Column("institution", sa.String(length=255), nullable=True),
        sa.Column(
            "is_liability",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
    op.create_index("ix_accounts_workspace_id", "accounts", ["workspace_id"])

    op.create_table(
        "account_balance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("balance_cents", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "uploaded_file_id",
            sa.Integer(),
            sa.ForeignKey("uploaded_files.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_workspace_balance_snapshot_date",
        "account_balance_snapshots",
        ["workspace_id", "as_of_date"],
    )
    op.create_index(
        "ix_account_balance_snapshot_date",
        "account_balance_snapshots",
        ["account_id", "as_of_date"],
    )

    with op.batch_alter_table("import_jobs") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_import_jobs_account_id_accounts",
            "accounts",
            ["account_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("import_jobs") as batch_op:
        batch_op.drop_constraint(
            "fk_import_jobs_account_id_accounts",
            type_="foreignkey",
        )
        batch_op.drop_column("account_id")

    op.drop_table("account_balance_snapshots")
    op.drop_table("accounts")
