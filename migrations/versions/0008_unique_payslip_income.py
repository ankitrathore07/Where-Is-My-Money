"""enforce one confirmed income record per payslip

Revision ID: 0008_unique_payslip_income
Revises: 0007_categorization_constraints
Create Date: 2026-08-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_unique_payslip_income"
down_revision = "0007_categorization_constraints"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "select payslip_id from income_records where payslip_id is not null "
            "group by payslip_id having count(*) > 1 limit 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "duplicate income records reference one payslip; remove duplicates before upgrading"
        )

    op.drop_index("ix_income_records_payslip_id", table_name="income_records")
    op.create_index(
        "uix_income_records_payslip_id",
        "income_records",
        ["payslip_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uix_income_records_payslip_id", table_name="income_records")
    op.create_index(
        "ix_income_records_payslip_id",
        "income_records",
        ["payslip_id"],
        unique=False,
    )
