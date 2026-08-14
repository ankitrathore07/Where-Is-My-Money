"""add stable institution identities for provider-aware imports

Revision ID: 0010_provider_aware_transaction_imports
Revises: 0009_account_statement_imports
Create Date: 2026-08-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_provider_aware_transaction_imports"
down_revision = "0009_account_statement_imports"
branch_labels = None
depends_on = None

INSTITUTION_KEY_CHECK = (
    "institution_key IS NULL OR institution_key IN "
    "('chase', 'bank_of_america', 'citi', 'capital_one', "
    "'american_express', 'discover', 'wells_fargo', 'other')"
)


def upgrade():
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("institution_key", sa.String(length=50), nullable=True))
        batch_op.create_check_constraint("ck_accounts_institution_key", INSTITUTION_KEY_CHECK)


def downgrade():
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint("ck_accounts_institution_key", type_="check")
        batch_op.drop_column("institution_key")
