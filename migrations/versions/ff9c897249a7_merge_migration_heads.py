"""merge migration heads

Revision ID: ff9c897249a7
Revises: 5b182d9a901f, c82f4a19d3b1
Create Date: 2026-10-07 19:53:04.955869

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff9c897249a7'
down_revision = ('5b182d9a901f', 'c82f4a19d3b1')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
