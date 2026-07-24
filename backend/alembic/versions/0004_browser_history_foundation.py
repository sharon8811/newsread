"""browser history foundation

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23 23:50:00.000000

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_browser_connections_user_id", "browser_connections", ["user_id"])
    op.create_index(
        "ix_browser_connections_token_prefix",
        "browser_connections",
        ["token_prefix"],
        unique=True,
    )
    op.create_index("ix_browser_connections_revoked_at", "browser_connections", ["revoked_at"])

    op.create_table(
        "browser_history_settings",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("retention_days", sa.Integer(), server_default="90", nullable=True),
        sa.Column("sync_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days IN (30, 90, 365)",
            name="ck_browser_history_retention_days",
        ),
        sa.CheckConstraint(
            "sync_revision >= 0",
            name="ck_browser_history_sync_revision",
        ),
    )

    op.create_table(
        "browser_history_domain_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("match_subdomains", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('exclude', 'metadata_only')",
            name="ck_browser_history_domain_rule_mode",
        ),
        sa.UniqueConstraint("user_id", "hostname", "match_subdomains"),
    )
    op.create_index(
        "ix_browser_history_domain_rules_user_id",
        "browser_history_domain_rules",
        ["user_id"],
    )

    op.create_table(
        "browser_history_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("text_excerpt", sa.Text(), server_default="", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("first_visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("visit_count >= 0", name="ck_browser_history_page_visit_count"),
        sa.UniqueConstraint("user_id", "url_hash"),
    )
    op.create_index("ix_browser_history_pages_user_id", "browser_history_pages", ["user_id"])
    op.create_index(
        "ix_browser_history_user_last_visited",
        "browser_history_pages",
        ["user_id", "last_visited_at"],
    )
    op.create_index(
        "ix_browser_history_user_hostname",
        "browser_history_pages",
        ["user_id", "hostname"],
    )

    op.create_table(
        "browser_history_page_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            sa.Integer(),
            sa.ForeignKey("browser_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first_visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_visited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visit_count >= 0",
            name="ck_browser_history_connection_visit_count",
        ),
        sa.UniqueConstraint("page_id", "connection_id"),
    )
    op.create_index(
        "ix_browser_history_page_connections_page_id",
        "browser_history_page_connections",
        ["page_id"],
    )
    op.create_index(
        "ix_browser_history_page_connections_connection_id",
        "browser_history_page_connections",
        ["connection_id"],
    )

    op.create_table(
        "browser_history_embeddings",
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_pages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "browser_history_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=253), server_default="", nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('page', 'domain', 'all')",
            name="ck_browser_history_deletion_scope",
        ),
        sa.CheckConstraint("revision > 0", name="ck_browser_history_deletion_revision"),
        sa.UniqueConstraint("user_id", "scope", "scope_key"),
    )
    op.create_index(
        "ix_browser_history_deletions_user_id",
        "browser_history_deletions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_browser_history_deletions_user_id",
        table_name="browser_history_deletions",
    )
    op.drop_table("browser_history_deletions")
    op.drop_table("browser_history_embeddings")
    op.drop_index(
        "ix_browser_history_page_connections_connection_id",
        table_name="browser_history_page_connections",
    )
    op.drop_index(
        "ix_browser_history_page_connections_page_id",
        table_name="browser_history_page_connections",
    )
    op.drop_table("browser_history_page_connections")
    op.drop_index(
        "ix_browser_history_user_hostname",
        table_name="browser_history_pages",
    )
    op.drop_index(
        "ix_browser_history_user_last_visited",
        table_name="browser_history_pages",
    )
    op.drop_index("ix_browser_history_pages_user_id", table_name="browser_history_pages")
    op.drop_table("browser_history_pages")
    op.drop_index(
        "ix_browser_history_domain_rules_user_id",
        table_name="browser_history_domain_rules",
    )
    op.drop_table("browser_history_domain_rules")
    op.drop_table("browser_history_settings")
    op.drop_index(
        "ix_browser_connections_revoked_at",
        table_name="browser_connections",
    )
    op.drop_index(
        "ix_browser_connections_token_prefix",
        table_name="browser_connections",
    )
    op.drop_index("ix_browser_connections_user_id", table_name="browser_connections")
    op.drop_table("browser_connections")
