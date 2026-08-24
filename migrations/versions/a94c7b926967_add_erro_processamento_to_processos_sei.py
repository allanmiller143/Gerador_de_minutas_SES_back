"""add erro_processamento to processos_sei

Revision ID: a94c7b926967
Revises: 3b6e74ba7132
Create Date: 2026-07-31 12:00:29.846614

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a94c7b926967'
down_revision = '3b6e74ba7132'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('processos_sei')]
    if 'erro_processamento' not in columns:
        with op.batch_alter_table('processos_sei', schema=None) as batch_op:
            batch_op.add_column(sa.Column('erro_processamento', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('processos_sei')]
    if 'erro_processamento' in columns:
        with op.batch_alter_table('processos_sei', schema=None) as batch_op:
            batch_op.drop_column('erro_processamento')
