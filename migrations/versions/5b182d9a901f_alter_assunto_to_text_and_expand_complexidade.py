"""alter assunto to text and expand complexidade

Revision ID: 5b182d9a901f
Revises: 4ab74eceb96d
Create Date: 2026-10-01 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5b182d9a901f'
down_revision = '4ab74eceb96d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        batch_op.alter_column('assunto',
                              existing_type=sa.String(length=200),
                              type_=sa.Text(),
                              existing_nullable=False)
        batch_op.alter_column('complexidade',
                              existing_type=sa.String(length=10),
                              type_=sa.String(length=50),
                              existing_nullable=True)


def downgrade():
    with op.batch_alter_table('processos_sei', schema=None) as batch_op:
        batch_op.alter_column('complexidade',
                              existing_type=sa.String(length=50),
                              type_=sa.String(length=10),
                              existing_nullable=True)
        batch_op.alter_column('assunto',
                              existing_type=sa.Text(),
                              type_=sa.String(length=200),
                              existing_nullable=False)
