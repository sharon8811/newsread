"""instance metrics: system-key llm metering, activity days, processing events

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-01 18:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # System-key calls join llm_usage: billing_source separates the bills,
    # user_id becomes nullable (batch work) and survives account deletion so
    # instance totals keep their history. Existing rows are all BYO ('user').
    op.add_column(
        "llm_usage",
        sa.Column("billing_source", sa.String(length=8), nullable=False, server_default="user"),
    )
    op.alter_column("llm_usage", "user_id", existing_type=sa.Integer(), nullable=True)
    op.drop_constraint("llm_usage_user_id_fkey", "llm_usage", type_="foreignkey")
    op.create_foreign_key(
        "llm_usage_user_id_fkey", "llm_usage", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])

    op.create_table(
        "user_activity_days",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "day"),
    )
    op.create_index("ix_user_activity_days_day", "user_activity_days", ["day"])

    op.create_table(
        "article_processing_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("feed_id", sa.Integer(), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("detail", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_processing_events_stage_created",
        "article_processing_events",
        ["stage", "created_at"],
    )

    # Date-range indexes for instance-wide trend queries.
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_subscriptions_created_at", "subscriptions", ["created_at"])
    op.create_index("ix_articles_fetched_at", "articles", ["fetched_at"])
    op.create_index(
        "ix_user_article_states_read_at",
        "user_article_states",
        ["read_at"],
        postgresql_where=sa.text("read_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_article_states_read_at", table_name="user_article_states")
    op.drop_index("ix_articles_fetched_at", table_name="articles")
    op.drop_index("ix_subscriptions_created_at", table_name="subscriptions")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index(
        "ix_article_processing_events_stage_created", table_name="article_processing_events"
    )
    op.drop_table("article_processing_events")
    op.drop_index("ix_user_activity_days_day", table_name="user_activity_days")
    op.drop_table("user_activity_days")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_constraint("llm_usage_user_id_fkey", "llm_usage", type_="foreignkey")
    op.create_foreign_key(
        "llm_usage_user_id_fkey", "llm_usage", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.execute("DELETE FROM llm_usage WHERE user_id IS NULL")
    op.alter_column("llm_usage", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("llm_usage", "billing_source")
