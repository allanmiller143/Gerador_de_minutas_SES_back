"""add standard text categories and texts

Revision ID: c82f4a19d3b1
Revises: 0d0a33a45e21
Create Date: 2026-10-02 21:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c82f4a19d3b1'
down_revision = '0d0a33a45e21'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('categorias_textos_padroes'):
        op.create_table(
            'categorias_textos_padroes',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('nome', sa.String(length=255), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
    if not inspector.has_table('textos_padroes'):
        op.create_table(
            'textos_padroes',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('titulo', sa.String(length=255), nullable=False),
            sa.Column('conteudo', sa.Text(), nullable=False),
            sa.Column('categoria_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ['categoria_id'],
                ['categorias_textos_padroes.id'],
                ondelete='SET NULL',
            ),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('textos_padroes'):
        op.drop_table('textos_padroes')
    if inspector.has_table('categorias_textos_padroes'):
        op.drop_table('categorias_textos_padroes')
