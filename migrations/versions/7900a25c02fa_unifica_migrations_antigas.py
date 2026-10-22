"""unifica migrations antigas

Revision ID: 7900a25c02fa
Revises: 5b182d9a901f, c82f4a19d3b1
Create Date: 2026-10-07 13:21:33.532286

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7900a25c02fa'
down_revision = ('5b182d9a901f', 'c82f4a19d3b1')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
