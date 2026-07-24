"""browser history encrypted content storage foundation

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-24 18:00:00.000000

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_history_user_keys",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("data_key_version", sa.Integer(), primary_key=True),
        sa.Column("wrapped_data_key", sa.LargeBinary(), nullable=False),
        sa.Column("wrap_alg", sa.String(length=32), nullable=False),
        sa.Column("wrapping_key_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "data_key_version > 0",
            name="ck_browser_history_user_key_data_version",
        ),
        sa.CheckConstraint(
            "wrapping_key_version > 0",
            name="ck_browser_history_user_key_wrapping_version",
        ),
        sa.CheckConstraint(
            "wrap_alg = 'AES-256-GCM'",
            name="ck_browser_history_user_key_wrap_alg",
        ),
    )

    op.create_table(
        "browser_history_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_hash", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("storage_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("source_host", sa.String(length=253), nullable=True),
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
            "image_hash ~ '^[0-9a-f]{64}$'",
            name="ck_browser_history_image_hash",
        ),
        sa.CheckConstraint(
            "storage_status IN ('pending', 'ready', 'failed', 'deleting')",
            name="ck_browser_history_image_storage_status",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0 AND byte_size > 0",
            name="ck_browser_history_image_dimensions",
        ),
        sa.UniqueConstraint("user_id", "image_hash"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_browser_history_images_user_id", "browser_history_images", ["user_id"])

    op.create_table(
        "browser_history_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("storage_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("text_excerpt", sa.Text(), server_default="", nullable=False),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("extraction_version", sa.String(length=64), nullable=False),
        sa.Column(
            "lead_image_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_images.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_browser_history_document_hash",
        ),
        sa.CheckConstraint(
            "storage_status IN ('pending', 'ready', 'failed', 'deleting')",
            name="ck_browser_history_document_storage_status",
        ),
        sa.CheckConstraint(
            "byte_size >= 0 AND character_count >= 0",
            name="ck_browser_history_document_sizes",
        ),
        sa.UniqueConstraint("user_id", "content_hash"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_browser_history_documents_user_id",
        "browser_history_documents",
        ["user_id"],
    )
    op.create_index(
        "ix_browser_history_documents_lead_image_id",
        "browser_history_documents",
        ["lead_image_id"],
    )
    op.create_index(
        "ix_browser_history_documents_user_status",
        "browser_history_documents",
        ["user_id", "storage_status"],
    )
    op.create_index(
        "ix_browser_history_documents_search_tsv",
        "browser_history_documents",
        ["search_tsv"],
        postgresql_using="gin",
    )

    op.add_column(
        "browser_history_pages",
        sa.Column("current_document_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "browser_history_pages",
        sa.Column("favicon_image_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_browser_history_pages_current_document",
        "browser_history_pages",
        "browser_history_documents",
        ["current_document_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_browser_history_pages_favicon_image",
        "browser_history_pages",
        "browser_history_images",
        ["favicon_image_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_browser_history_pages_current_document_id",
        "browser_history_pages",
        ["current_document_id"],
    )
    op.create_index(
        "ix_browser_history_pages_favicon_image_id",
        "browser_history_pages",
        ["favicon_image_id"],
    )

    op.create_table(
        "browser_history_page_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "page_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_browser_history_page_document_seen_order",
        ),
        sa.UniqueConstraint("page_id", "document_id"),
    )
    op.create_index(
        "ix_browser_history_page_documents_page_id",
        "browser_history_page_documents",
        ["page_id"],
    )
    op.create_index(
        "ix_browser_history_page_documents_document_id",
        "browser_history_page_documents",
        ["document_id"],
    )
    op.create_index(
        "ix_browser_history_page_documents_document_last_seen",
        "browser_history_page_documents",
        ["document_id", "last_seen_at"],
    )

    op.create_table(
        "browser_history_document_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("block_start_id", sa.String(length=32), nullable=True),
        sa.Column("block_end_id", sa.String(length=32), nullable=True),
        sa.Column(
            "embedded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_browser_history_document_embedding_chunk",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_browser_history_document_embedding_hash",
        ),
        sa.UniqueConstraint("document_id", "chunk_index", "model"),
    )
    op.create_index(
        "ix_browser_history_document_embeddings_document",
        "browser_history_document_embeddings",
        ["document_id"],
    )

    op.create_table(
        "browser_history_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("browser_history_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("markdown", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
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
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_browser_history_summary_hash",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'generating', 'ready', 'failed')",
            name="ck_browser_history_summary_status",
        ),
        sa.UniqueConstraint(
            "document_id",
            "model",
            "prompt_version",
            "input_hash",
        ),
    )
    op.create_index(
        "ix_browser_history_summaries_document",
        "browser_history_summaries",
        ["document_id"],
    )

    op.create_table(
        "browser_history_object_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Deliberately not a FK: deletion work must survive account deletion.
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_hash", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "object_type IN ('document', 'image')",
            name="ck_browser_history_object_deletion_type",
        ),
        sa.CheckConstraint(
            "object_hash ~ '^[0-9a-f]{64}$'",
            name="ck_browser_history_object_deletion_hash",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_browser_history_object_deletion_attempts",
        ),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_browser_history_object_deletions_owner_user_id",
        "browser_history_object_deletions",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_browser_history_object_deletions_next_attempt_at",
        "browser_history_object_deletions",
        ["next_attempt_at"],
    )

    op.add_column(
        "conversations",
        sa.Column("history_document_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_history_document",
        "conversations",
        "browser_history_documents",
        ["history_document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_conversations_history_document_id",
        "conversations",
        ["history_document_id"],
    )
    op.create_index(
        "uq_conversations_history_document_user",
        "conversations",
        ["history_document_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("history_document_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversations_history_document_user",
        table_name="conversations",
    )
    op.drop_index(
        "ix_conversations_history_document_id",
        table_name="conversations",
    )
    op.drop_constraint(
        "fk_conversations_history_document",
        "conversations",
        type_="foreignkey",
    )
    op.drop_column("conversations", "history_document_id")

    op.drop_index(
        "ix_browser_history_object_deletions_next_attempt_at",
        table_name="browser_history_object_deletions",
    )
    op.drop_index(
        "ix_browser_history_object_deletions_owner_user_id",
        table_name="browser_history_object_deletions",
    )
    op.drop_table("browser_history_object_deletions")

    op.drop_index(
        "ix_browser_history_summaries_document",
        table_name="browser_history_summaries",
    )
    op.drop_table("browser_history_summaries")

    op.drop_index(
        "ix_browser_history_document_embeddings_document",
        table_name="browser_history_document_embeddings",
    )
    op.drop_table("browser_history_document_embeddings")

    op.drop_index(
        "ix_browser_history_page_documents_document_last_seen",
        table_name="browser_history_page_documents",
    )
    op.drop_index(
        "ix_browser_history_page_documents_document_id",
        table_name="browser_history_page_documents",
    )
    op.drop_index(
        "ix_browser_history_page_documents_page_id",
        table_name="browser_history_page_documents",
    )
    op.drop_table("browser_history_page_documents")

    op.drop_index(
        "ix_browser_history_pages_favicon_image_id",
        table_name="browser_history_pages",
    )
    op.drop_index(
        "ix_browser_history_pages_current_document_id",
        table_name="browser_history_pages",
    )
    op.drop_constraint(
        "fk_browser_history_pages_favicon_image",
        "browser_history_pages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_browser_history_pages_current_document",
        "browser_history_pages",
        type_="foreignkey",
    )
    op.drop_column("browser_history_pages", "favicon_image_id")
    op.drop_column("browser_history_pages", "current_document_id")

    op.drop_index(
        "ix_browser_history_documents_search_tsv",
        table_name="browser_history_documents",
    )
    op.drop_index(
        "ix_browser_history_documents_user_status",
        table_name="browser_history_documents",
    )
    op.drop_index(
        "ix_browser_history_documents_lead_image_id",
        table_name="browser_history_documents",
    )
    op.drop_index(
        "ix_browser_history_documents_user_id",
        table_name="browser_history_documents",
    )
    op.drop_table("browser_history_documents")

    op.drop_index(
        "ix_browser_history_images_user_id",
        table_name="browser_history_images",
    )
    op.drop_table("browser_history_images")
    op.drop_table("browser_history_user_keys")
