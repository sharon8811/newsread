"""instance roles and account status

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-01 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=8), nullable=False, server_default="user"),
    )
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_column("users", "status")
    op.drop_column("users", "role")
