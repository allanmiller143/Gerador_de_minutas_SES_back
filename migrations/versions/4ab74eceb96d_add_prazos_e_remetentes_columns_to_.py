"""add prazos e remetentes columns to processo_sei

Revision ID: 4ab74eceb96d
Revises: e17e0f461c85
Create Date: 2026-09-30 17:43:41.503674

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4ab74eceb96d'
down_revision = 'e17e0f461c85'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        if 'data_emissao_documento' not in cols:
            batch_op.add_column(sa.Column('data_emissao_documento', sa.DateTime(), nullable=True))
        if 'prazo_legal_dias' not in cols:
            batch_op.add_column(sa.Column('prazo_legal_dias', sa.Integer(), nullable=True))
        if 'data_inicio_analise' not in cols:
            batch_op.add_column(sa.Column('data_inicio_analise', sa.DateTime(), nullable=True))
        if 'remetente' not in cols:
            batch_op.add_column(sa.Column('remetente', sa.String(length=150), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        if 'remetente' in cols:
            batch_op.drop_column('remetente')
        if 'data_inicio_analise' in cols:
            batch_op.drop_column('data_inicio_analise')
        if 'prazo_legal_dias' in cols:
            batch_op.drop_column('prazo_legal_dias')
        if 'data_emissao_documento' in cols:
            batch_op.drop_column('data_emissao_documento')
