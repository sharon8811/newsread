"""user tiers and monthly article allowances

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-01 22:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("monthly_article_allowance", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.add_column("users", sa.Column("tier_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "users_tier_id_fkey", "users", "tiers", ["tier_id"], ["id"], ondelete="SET NULL"
    )
    op.create_table(
        "user_quota_periods",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tier_key_snapshot", sa.String(length=16), nullable=False),
        sa.Column("allowance_snapshot", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "period"),
    )
    op.create_table(
        "user_article_charges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "article_id"),
    )
    op.create_index(
        "ix_user_article_charges_user_period", "user_article_charges", ["user_id", "period"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_article_charges_user_period", table_name="user_article_charges")
    op.drop_table("user_article_charges")
    op.drop_table("user_quota_periods")
    op.drop_constraint("users_tier_id_fkey", "users", type_="foreignkey")
    op.drop_column("users", "tier_id")
    op.drop_table("tiers")
