"""create imports and transactions tables

Revision ID: 0002_imports_transactions
Revises: 0001_create_users_workspaces
Create Date: 2026-08-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_imports_transactions"
down_revision = "0001_create_users_workspaces"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "retention_choice", sa.String(length=20), nullable=False, server_default="retain"
        ),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_uploaded_files_workspace_id", "uploaded_files", ["workspace_id"])

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="expense"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_categories_workspace_id", "categories", ["workspace_id"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "uploaded_file_id", sa.Integer(), sa.ForeignKey("uploaded_files.id"), nullable=True
        ),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("column_mapping", sa.JSON(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
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
    op.create_index("ix_import_jobs_workspace_id", "import_jobs", ["workspace_id"])

    op.create_table(
        "merchant_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("merchant_pattern", sa.String(length=255), nullable=False),
        sa.Column("normalized_merchant", sa.String(length=255), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_merchant_rules_workspace_id", "merchant_rules", ["workspace_id"])
    op.create_index("ix_merchant_rules_category_id", "merchant_rules", ["category_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("normalized_merchant", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column(
            "categorization_source",
            sa.String(length=50),
            nullable=False,
            server_default="uncategorized",
        ),
        sa.Column("duplicate_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("import_job_id", sa.Integer(), sa.ForeignKey("import_jobs.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "duplicate_fingerprint",
            name="uix_workspace_duplicate_fingerprint",
        ),
    )
    op.create_index("ix_transactions_workspace_id", "transactions", ["workspace_id"])
    op.create_index("ix_transactions_category_id", "transactions", ["category_id"])
    op.create_index("ix_workspace_transaction_date", "transactions", ["workspace_id", "date"])
    op.create_index(
        "ix_workspace_transaction_category", "transactions", ["workspace_id", "category_id"]
    )
    op.create_index(
        "ix_workspace_normalized_merchant",
        "transactions",
        ["workspace_id", "normalized_merchant"],
    )


def downgrade():
    op.drop_table("transactions")
    op.drop_table("merchant_rules")
    op.drop_table("import_jobs")
    op.drop_table("categories")
    op.drop_table("uploaded_files")
