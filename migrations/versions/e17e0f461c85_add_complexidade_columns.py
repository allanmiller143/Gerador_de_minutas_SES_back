"""add complexidade columns

Revision ID: e17e0f461c85
Revises: 0d0a33a45e21
Create Date: 2026-09-26 19:07:29.400421

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e17e0f461c85'
down_revision = '0d0a33a45e21'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        if 'complexidade' not in cols:
            batch_op.add_column(sa.Column('complexidade', sa.String(length=10), nullable=True))
        if 'complexidade_justificativa' not in cols:
            batch_op.add_column(sa.Column('complexidade_justificativa', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        if 'complexidade_justificativa' in cols:
            batch_op.drop_column('complexidade_justificativa')
        if 'complexidade' in cols:
            batch_op.drop_column('complexidade')
