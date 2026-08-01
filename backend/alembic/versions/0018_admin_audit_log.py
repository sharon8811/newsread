"""admin audit log

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-01 20:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])
    op.create_index(
        "ix_admin_audit_log_target_created", "admin_audit_log", ["target_user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_audit_log_target_created", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_log_created_at", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
