"""enforce categorization data invariants

Revision ID: 0007_categorization_constraints
Revises: 0006_builtin_categories
Create Date: 2026-08-09 00:00:00.000000
"""

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0007_categorization_constraints"
down_revision = "0006_builtin_categories"
branch_labels = None
depends_on = None

BUILTIN_CATEGORIES = (
    ("Income", "income"),
    ("Transfers", "transfer"),
    ("Housing", "expense"),
    ("Utilities", "expense"),
    ("Groceries", "expense"),
    ("Dining & Drinks", "expense"),
    ("Transportation", "expense"),
    ("Shopping", "expense"),
    ("Entertainment", "expense"),
    ("Software & Online Services", "expense"),
    ("Health & Fitness", "expense"),
    ("Insurance", "expense"),
    ("Education", "expense"),
    ("Travel", "expense"),
    ("Personal Care", "expense"),
    ("Pets", "expense"),
    ("Childcare", "expense"),
    ("Gifts & Donations", "expense"),
    ("Taxes & Fees", "expense"),
    ("Cash & ATM", "expense"),
    ("Uncategorized", "expense"),
)

PR4_RENAMES = {
    "Dining": "Dining & Drinks",
    "Health": "Health & Fitness",
}


def _name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _backfill_category_keys(connection) -> None:
    rows = connection.execute(sa.text("select id, workspace_id, name from categories")).mappings()
    seen: dict[tuple[int | None, str], int] = {}
    for row in rows:
        key = _name_key(row["name"])
        scope_key = (row["workspace_id"], key)
        if scope_key in seen:
            raise RuntimeError(
                "duplicate category name after normalization; rename duplicate categories "
                "before upgrading"
            )
        seen[scope_key] = row["id"]
        connection.execute(
            sa.text("update categories set name_key = :name_key where id = :category_id"),
            {"name_key": key, "category_id": row["id"]},
        )


def _check_duplicate_merchant_keys(connection) -> None:
    duplicate = connection.execute(
        sa.text(
            "select workspace_id, merchant_pattern from merchant_rules "
            "group by workspace_id, merchant_pattern having count(*) > 1 limit 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate merchant rule key; remove duplicate workspace merchant rules "
            "before upgrading"
        )


def _expand_builtin_categories(connection) -> None:
    for old_name, new_name in PR4_RENAMES.items():
        connection.execute(
            sa.text(
                "update categories set name = :new_name "
                "where workspace_id is null and name = :old_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )

    for name, kind in BUILTIN_CATEGORIES:
        exists = connection.execute(
            sa.text("select 1 from categories where workspace_id is null and name = :name limit 1"),
            {"name": name},
        ).first()
        if exists is None:
            connection.execute(
                sa.text(
                    "insert into categories (workspace_id, name, kind) values (null, :name, :kind)"
                ),
                {"name": name, "kind": kind},
            )


def _remap_removed_category_references(connection, removed_names: set[str]) -> None:
    uncategorized_id = connection.execute(
        sa.text(
            "select id from categories "
            "where workspace_id is null and name = 'Uncategorized' limit 1"
        )
    ).scalar_one()
    for name in removed_names:
        removed_id = connection.execute(
            sa.text(
                "select id from categories where workspace_id is null and name = :name limit 1"
            ),
            {"name": name},
        ).scalar_one_or_none()
        if removed_id is None:
            continue
        for table_name in ("transactions", "merchant_rules", "budgets"):
            connection.execute(
                sa.text(
                    f"update {table_name} set category_id = :replacement "  # noqa: S608
                    "where category_id = :removed"
                ),
                {"replacement": uncategorized_id, "removed": removed_id},
            )


def upgrade():
    connection = op.get_bind()
    op.add_column("categories", sa.Column("name_key", sa.String(length=100), nullable=True))
    _expand_builtin_categories(connection)
    _backfill_category_keys(connection)
    _check_duplicate_merchant_keys(connection)

    with op.batch_alter_table("categories") as batch_op:
        batch_op.alter_column("name_key", existing_type=sa.String(length=100), nullable=False)

    op.create_index(
        "uix_custom_category_name_key",
        "categories",
        ["workspace_id", "name_key"],
        unique=True,
        sqlite_where=sa.text("workspace_id IS NOT NULL"),
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )
    op.create_index(
        "uix_builtin_category_name_key",
        "categories",
        ["name_key"],
        unique=True,
        sqlite_where=sa.text("workspace_id IS NULL"),
        postgresql_where=sa.text("workspace_id IS NULL"),
    )

    op.add_column(
        "transactions",
        sa.Column("is_subscription", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "merchant_rules",
        sa.Column("is_subscription", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "merchant_rules",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.create_unique_constraint(
            "uix_workspace_merchant_pattern", ["workspace_id", "merchant_pattern"]
        )


def downgrade():
    connection = op.get_bind()
    with op.batch_alter_table("merchant_rules") as batch_op:
        batch_op.drop_constraint("uix_workspace_merchant_pattern", type_="unique")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("is_subscription")

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_column("is_subscription")

    op.drop_index("uix_builtin_category_name_key", table_name="categories")
    op.drop_index("uix_custom_category_name_key", table_name="categories")

    original_names = {
        "Uncategorized",
        "Groceries",
        "Dining & Drinks",
        "Housing",
        "Utilities",
        "Transportation",
        "Shopping",
        "Entertainment",
        "Health & Fitness",
        "Income",
        "Transfers",
    }
    added_names = {name for name, _ in BUILTIN_CATEGORIES} - original_names
    _remap_removed_category_references(connection, added_names)
    for name in added_names:
        connection.execute(
            sa.text("delete from categories where workspace_id is null and name = :name"),
            {"name": name},
        )
    for old_name, new_name in PR4_RENAMES.items():
        connection.execute(
            sa.text(
                "update categories set name = :old_name "
                "where workspace_id is null and name = :new_name"
            ),
            {"old_name": old_name, "new_name": new_name},
        )

    with op.batch_alter_table("categories") as batch_op:
        batch_op.drop_column("name_key")
