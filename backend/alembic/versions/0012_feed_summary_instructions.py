"""per-feed summary instructions

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-26 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("summary_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("feeds", "summary_instructions")
