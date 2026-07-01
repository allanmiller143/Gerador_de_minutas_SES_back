"""merge migration heads

Revision ID: a80ace40ed0c
Revises: 1f9b279a5a38, 49af975cc515
Create Date: 2026-07-01 10:13:52.843815

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a80ace40ed0c'
down_revision = ('1f9b279a5a38', '49af975cc515')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
