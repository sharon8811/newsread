"""track intentionally skipped summaries

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17 23:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("summary_skipped_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("articles", "summary_skipped_reason")
