"""Ajout de la colonne date_inscription à la table User

Revision ID: 0accc63bc31e
Revises: e7380afa85ea
Create Date: 2025-05-20 11:48:43.447318
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0accc63bc31e'
down_revision = 'e7380afa85ea'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('article', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        # batch_op.alter_column('content', existing_type=sa.TEXT(), nullable=True)
        # batch_op.alter_column('url', existing_type=sa.VARCHAR(length=500), type_=sa.String(length=255), existing_nullable=True)
        # batch_op.alter_column('category', existing_type=sa.VARCHAR(length=100), type_=sa.String(length=50), nullable=True)
        # batch_op.alter_column('published_at', existing_type=sa.VARCHAR(length=100), type_=sa.String(length=50), existing_nullable=True)
        batch_op.create_unique_constraint('uq_article_url', ['url'])

    with op.batch_alter_table('question', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        # batch_op.alter_column('categorie', existing_type=sa.VARCHAR(length=100), type_=sa.String(length=50), existing_nullable=False)
        batch_op.alter_column('valide', existing_type=sa.BOOLEAN(), nullable=True)
        batch_op.alter_column('is_refused', existing_type=sa.BOOLEAN(), nullable=True)
        batch_op.alter_column('article_id', existing_type=sa.INTEGER(), nullable=True)

    with op.batch_alter_table('reponse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('est_active', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('date_creation', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('date_modification', sa.DateTime(), nullable=True))
        # batch_op.alter_column('etat', existing_type=sa.VARCHAR(length=50), type_=sa.String(length=20), existing_nullable=True)
        batch_op.create_unique_constraint('unique_active_response', ['user_id', 'question_id', 'est_active'])
        batch_op.drop_column('skipped')
        batch_op.drop_column('created_at')

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('date_inscription', sa.DateTime(), nullable=True))
        # batch_op.alter_column('username', existing_type=sa.VARCHAR(length=100), type_=sa.String(length=80), existing_nullable=False)
        batch_op.alter_column('password_hash', existing_type=sa.VARCHAR(length=128), nullable=True)


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.VARCHAR(length=128), nullable=False)
        # batch_op.alter_column('username', existing_type=sa.String(length=80), type_=sa.VARCHAR(length=100), existing_nullable=False)
        batch_op.drop_column('date_inscription')

    with op.batch_alter_table('reponse', schema=None) as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DATETIME(), nullable=True))
        batch_op.add_column(sa.Column('skipped', sa.BOOLEAN(), nullable=True))
        batch_op.drop_constraint('unique_active_response', type_='unique')
        # batch_op.alter_column('etat', existing_type=sa.String(length=20), type_=sa.VARCHAR(length=50), existing_nullable=True)
        batch_op.drop_column('date_modification')
        batch_op.drop_column('date_creation')
        batch_op.drop_column('est_active')

    with op.batch_alter_table('question', schema=None) as batch_op:
        batch_op.alter_column('article_id', existing_type=sa.INTEGER(), nullable=False)
        batch_op.alter_column('is_refused', existing_type=sa.BOOLEAN(), nullable=False)
        batch_op.alter_column('valide', existing_type=sa.BOOLEAN(), nullable=False)
        # batch_op.alter_column('categorie', existing_type=sa.String(length=50), type_=sa.VARCHAR(length=100), existing_nullable=False)
        batch_op.drop_column('created_at')

    with op.batch_alter_table('article', schema=None) as batch_op:
        # batch_op.drop_constraint('uq_article_url', type_='unique')
        # batch_op.alter_column('published_at', existing_type=sa.String(length=50), type_=sa.VARCHAR(length=100), existing_nullable=True)
        # batch_op.alter_column('category', existing_type=sa.String(length=50), type_=sa.VARCHAR(length=100), nullable=False)
        # batch_op.alter_column('url', existing_type=sa.String(length=255), type_=sa.VARCHAR(length=500), existing_nullable=True)
        # batch_op.alter_column('content', existing_type=sa.TEXT(), nullable=False)
        batch_op.drop_column('created_at')
