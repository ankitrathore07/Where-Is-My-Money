"""add reviewed account statement balance imports

Revision ID: 0009_account_statement_imports
Revises: 0008_unique_payslip_income
Create Date: 2026-08-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_account_statement_imports"
down_revision = "0008_unique_payslip_income"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_statement_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_file_id",
            sa.Integer(),
            sa.ForeignKey("uploaded_files.id"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("statement_category", sa.String(length=50), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("candidate_fields", sa.JSON(), nullable=False),
        sa.Column("confirmed_fields", sa.JSON(), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "statement_category",
            "source_checksum",
            name="uix_statement_import_workspace_category_checksum",
        ),
    )
    op.create_index(
        "ix_account_statement_imports_workspace_id",
        "account_statement_imports",
        ["workspace_id"],
    )

    with op.batch_alter_table("account_balance_snapshots") as batch_op:
        batch_op.add_column(sa.Column("statement_import_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_balance_snapshot_statement_import",
            "account_statement_imports",
            ["statement_import_id"],
            ["id"],
        )
        batch_op.create_index(
            "uix_balance_snapshot_statement_import_id",
            ["statement_import_id"],
            unique=True,
        )


def downgrade():
    with op.batch_alter_table("account_balance_snapshots") as batch_op:
        batch_op.drop_index("uix_balance_snapshot_statement_import_id")
        batch_op.drop_constraint("fk_balance_snapshot_statement_import", type_="foreignkey")
        batch_op.drop_column("statement_import_id")

    op.drop_index(
        "ix_account_statement_imports_workspace_id",
        table_name="account_statement_imports",
    )
    op.drop_table("account_statement_imports")
