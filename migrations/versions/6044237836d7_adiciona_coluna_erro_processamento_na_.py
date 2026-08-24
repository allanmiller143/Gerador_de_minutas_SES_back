"""Adiciona coluna erro_processamento na tabela processos_sei

Revision ID: 6044237836d7
Revises: 3b6e74ba7132
Create Date: 2026-07-20 18:15:27.565567

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6044237836d7'
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
