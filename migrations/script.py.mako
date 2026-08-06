"""A very small subset of Alembic's script.py.mako for generated envs.
This template is lightweight and only used when autogenerate writes revision files
via `alembic revision --autogenerate`.
"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    pass


def downgrade():
    pass
