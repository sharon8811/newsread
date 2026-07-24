"""browser history sync rate limit state

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-24 02:10:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "browser_connections",
        sa.Column("sync_window_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "browser_connections",
        sa.Column(
            "sync_request_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("browser_connections", "sync_request_count")
    op.drop_column("browser_connections", "sync_window_started_at")
