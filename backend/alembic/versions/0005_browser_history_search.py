"""browser history full-text search

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-24 02:00:00.000000

"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index(
        "ix_browser_history_pages_search_tsv",
        table_name="browser_history_pages",
    )
    op.drop_column("browser_history_pages", "search_tsv")
