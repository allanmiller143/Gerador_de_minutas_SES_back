"""Adiciona campo prioridade ao Remetente

Revision ID: 3d5ebf4dedc9
Revises: 5b182d9a901f
Create Date: 2026-10-04 10:30:33.054600

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3d5ebf4dedc9'
down_revision = '5b182d9a901f'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    rem_cols = [c['name'] for c in inspector.get_columns('remetentes')]

    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        batch_op.alter_column('assunto',
               existing_type=sa.VARCHAR(length=200),
               type_=sa.Text(),
               existing_nullable=False)
        batch_op.alter_column('complexidade',
               existing_type=sa.VARCHAR(length=10),
               type_=sa.String(length=50),
               existing_nullable=True)

    if 'prioridade' not in rem_cols:
        with op.batch_alter_table('remetentes', schema=None) as batch_op:
            batch_op.add_column(sa.Column('prioridade', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    rem_cols = [c['name'] for c in inspector.get_columns('remetentes')]

    if 'prioridade' in rem_cols:
        with op.batch_alter_table('remetentes', schema=None) as batch_op:
            batch_op.drop_column('prioridade')

    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        batch_op.alter_column('complexidade',
               existing_type=sa.String(length=50),
               type_=sa.VARCHAR(length=10),
               existing_nullable=True)
        batch_op.alter_column('assunto',
               existing_type=sa.Text(),
               type_=sa.VARCHAR(length=200),
               existing_nullable=False)
