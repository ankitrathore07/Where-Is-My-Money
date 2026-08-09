"""seed built-in transaction categories

Revision ID: 0006_builtin_categories
Revises: 0005_accounts_balances
Create Date: 2026-08-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_builtin_categories"
down_revision = "0005_accounts_balances"
branch_labels = None
depends_on = None

BUILTIN_CATEGORIES = (
    ("Uncategorized", "expense"),
    ("Groceries", "expense"),
    ("Dining", "expense"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Health", "expense"),
    ("Income", "income"),
    ("Transfers", "transfer"),
)

categories = sa.table(
    "categories",
    sa.column("workspace_id", sa.Integer()),
    sa.column("name", sa.String()),
    sa.column("kind", sa.String()),
)


def upgrade():
    connection = op.get_bind()
    for name, kind in BUILTIN_CATEGORIES:
        exists = connection.scalar(
            sa.select(sa.literal(True)).where(
                categories.c.workspace_id.is_(None),
                categories.c.name == name,
                categories.c.kind == kind,
            )
        )
        if not exists:
            connection.execute(categories.insert().values(workspace_id=None, name=name, kind=kind))


def downgrade():
    connection = op.get_bind()
    for name, kind in BUILTIN_CATEGORIES:
        connection.execute(
            categories.delete().where(
                categories.c.workspace_id.is_(None),
                categories.c.name == name,
                categories.c.kind == kind,
            )
        )
