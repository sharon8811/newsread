"""summary translations

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-30 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("summary_language", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("translation_language", sa.String(length=16), nullable=True))
    op.create_table(
        "summary_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "language", "source_hash"),
    )
    op.create_index(
        "ix_summary_translations_article_id", "summary_translations", ["article_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_summary_translations_article_id", table_name="summary_translations")
    op.drop_table("summary_translations")
    op.drop_column("users", "translation_language")
    op.drop_column("articles", "summary_language")
