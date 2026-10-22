"""adiciona coluna alerta_ocr na tabela processos_sei

Revision ID: b2c7f8d22467
Revises: 7900a25c02fa
Create Date: 2026-10-07 13:22:09.223406

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c7f8d22467'
down_revision = '7900a25c02fa'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    if 'alerta_ocr' not in cols:
        with op.batch_alter_table('processos_sei', schema=None) as batch_op:
            batch_op.add_column(sa.Column('alerta_ocr', sa.Boolean(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = [c['name'] for c in inspector.get_columns('processos_sei')]
    if 'alerta_ocr' in cols:
        with op.batch_alter_table('processos_sei', schema=None) as batch_op:
            batch_op.drop_column('alerta_ocr')
