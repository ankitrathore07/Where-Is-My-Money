"""add tax refund and installment plan tags

Revision ID: 0012_tax_refund_and_installment_tags
Revises: 0011_transaction_tags_and_cadence
Create Date: 2026-08-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_tax_refund_and_installment_tags"
down_revision = "0011_transaction_tags_and_cadence"
branch_labels = None
depends_on = None

TAG_NAMES = ("Tax Refund", "Installment Plan")


def upgrade():
    connection = op.get_bind()
    for name in TAG_NAMES:
        connection.execute(
            sa.text(
                "insert into tags (workspace_id, name, name_key) values (null, :name, :name_key)"
            ),
            {"name": name, "name_key": " ".join(name.split()).casefold()},
        )


def downgrade():
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "delete from tags where workspace_id is null "
            "and name_key in ('tax refund', 'installment plan')"
        )
    )
