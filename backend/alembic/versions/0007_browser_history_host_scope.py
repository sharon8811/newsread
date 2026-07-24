"""browser history exact-host deletion scope

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-24 16:00:00.000000

"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_browser_history_deletion_scope",
        "browser_history_deletions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_browser_history_deletion_scope",
        "browser_history_deletions",
        "scope IN ('page', 'domain', 'host', 'all')",
    )


def downgrade() -> None:
    # Broadening 'host' to 'domain' only widens what stale captures are
    # rejected — the privacy-safe direction for deletion tombstones.
    op.execute("UPDATE browser_history_deletions SET scope = 'domain' WHERE scope = 'host'")
    op.drop_constraint(
        "ck_browser_history_deletion_scope",
        "browser_history_deletions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_browser_history_deletion_scope",
        "browser_history_deletions",
        "scope IN ('page', 'domain', 'all')",
    )
