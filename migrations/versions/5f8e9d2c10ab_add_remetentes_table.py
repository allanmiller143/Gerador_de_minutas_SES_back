"""add remetentes table

Revision ID: 5f8e9d2c10ab
Revises: a80ace40ed0c
Create Date: 2026-08-04 15:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5f8e9d2c10ab'
down_revision = 'a94c7b926967'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('remetentes'):
        op.create_table(
            'remetentes',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('prefixo', sa.String(length=50), nullable=False),
            sa.Column('nome_completo', sa.String(length=255), nullable=False),
            sa.Column('sigla', sa.String(length=50), nullable=False),
            sa.Column('cor', sa.String(length=50), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade():
    op.drop_table('remetentes')
