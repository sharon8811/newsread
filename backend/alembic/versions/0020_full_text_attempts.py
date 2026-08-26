"""count page fetches that yielded no prose

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-26 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column(
            "full_text_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Existing rows start at zero on purpose. Articles stranded by the old
    # fetch-once rule — page fetched, no prose, no summary, no skip reason —
    # become eligible again, which is the point: a browser render now recovers
    # the ones that were only ever behind a bot check.


def downgrade() -> None:
    op.drop_column("articles", "full_text_attempts")
