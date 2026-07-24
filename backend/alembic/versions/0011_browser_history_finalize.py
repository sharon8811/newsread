"""finalize browser history content storage

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-24 22:00:00.000000

"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The outbox intentionally has no owner FK so these rows survive a
    # cascading account deletion. Database triggers cover ORM deletes, bulk
    # deletes, retention, and future account-deletion entry points uniformly.
    op.execute(
        """
        CREATE FUNCTION enqueue_browser_history_object_deletion()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          INSERT INTO browser_history_object_deletions (
            owner_user_id,
            object_type,
            object_hash,
            object_key
          )
          VALUES (
            OLD.user_id,
            TG_ARGV[0],
            CASE
              WHEN TG_ARGV[0] = 'document'
                THEN to_jsonb(OLD)->>'content_hash'
              ELSE to_jsonb(OLD)->>'image_hash'
            END,
            OLD.object_key
          )
          ON CONFLICT (object_key) DO NOTHING;
          RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER browser_history_documents_enqueue_object_delete
        AFTER DELETE ON browser_history_documents
        FOR EACH ROW EXECUTE FUNCTION
          enqueue_browser_history_object_deletion('document')
        """
    )
    op.execute(
        """
        CREATE TRIGGER browser_history_images_enqueue_object_delete
        AFTER DELETE ON browser_history_images
        FOR EACH ROW EXECUTE FUNCTION
          enqueue_browser_history_object_deletion('image')
        """
    )

    op.create_table(
        "browser_history_embedding_usage",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("period_start", sa.Date(), primary_key=True),
        sa.Column("document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "document_count >= 0",
            name="ck_browser_history_embedding_usage_count",
        ),
    )
    op.create_table(
        "browser_history_gc_state",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.drop_table("browser_history_embeddings")
    op.drop_index(
        "ix_browser_history_pages_search_tsv",
        table_name="browser_history_pages",
    )
    op.drop_column("browser_history_pages", "search_tsv")
    op.drop_column("browser_history_pages", "content_hash")
    op.drop_column("browser_history_pages", "text_excerpt")
    op.drop_column("browser_history_pages", "text")


def downgrade() -> None:
    op.add_column(
        "browser_history_pages",
        sa.Column("text", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "browser_history_pages",
        sa.Column("text_excerpt", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "browser_history_pages",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.execute(
        "ALTER TABLE browser_history_pages ADD COLUMN search_tsv tsvector "
        "GENERATED ALWAYS AS ("
        "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('simple', coalesce(hostname, '')), 'B') || "
        "setweight(to_tsvector('english', coalesce(text, '')), 'C')"
        ") STORED"
    )
    op.create_index(
        "ix_browser_history_pages_search_tsv",
        "browser_history_pages",
        ["search_tsv"],
        postgresql_using="gin",
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
    op.drop_table("browser_history_gc_state")
    op.drop_table("browser_history_embedding_usage")

    op.execute(
        "DROP TRIGGER browser_history_images_enqueue_object_delete ON browser_history_images"
    )
    op.execute(
        "DROP TRIGGER browser_history_documents_enqueue_object_delete ON browser_history_documents"
    )
    op.execute("DROP FUNCTION enqueue_browser_history_object_deletion()")
