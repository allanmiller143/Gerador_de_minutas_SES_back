"""merge heads after sistema_prioridade

Revision ID: 6a9e2703b66f
Revises: 430fa6f34049, ff9c897249a7
Create Date: 2026-10-07 20:03:39.403502

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a9e2703b66f'
down_revision = ('430fa6f34049', 'ff9c897249a7')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
