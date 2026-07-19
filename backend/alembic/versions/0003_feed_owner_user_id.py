"""per-user hidden import feeds

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_feeds_owner_user_id", "feeds", ["owner_user_id"])
    op.create_foreign_key(
        "fk_feeds_owner_user_id_users",
        "feeds",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_feeds_owner_user_id_users", "feeds", type_="foreignkey")
    op.drop_constraint("uq_feeds_owner_user_id", "feeds", type_="unique")
    op.drop_column("feeds", "owner_user_id")
