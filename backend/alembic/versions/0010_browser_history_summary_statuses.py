"""align browser history summary states with the worker

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-24 22:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_browser_history_summary_status"
CURRENT_STATES = ("queued", "running", "ready", "error", "too_short")
LEGACY_STATES = ("queued", "generating", "ready", "failed")


def _status_check(states: tuple[str, ...]) -> str:
    values = ", ".join(f"'{state}'" for state in states)
    return f"status IN ({values})"


def upgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "browser_history_summaries",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE browser_history_summaries
            SET status = CASE status
                WHEN 'generating' THEN 'running'
                WHEN 'failed' THEN 'error'
                ELSE status
            END
            WHERE status IN ('generating', 'failed')
            """
        )
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "browser_history_summaries",
        _status_check(CURRENT_STATES),
    )


def downgrade() -> None:
    op.drop_constraint(
        CONSTRAINT_NAME,
        "browser_history_summaries",
        type_="check",
    )
    op.execute(
        sa.text(
            """
            UPDATE browser_history_summaries
            SET status = CASE status
                WHEN 'running' THEN 'generating'
                WHEN 'error' THEN 'failed'
                WHEN 'too_short' THEN 'failed'
                ELSE status
            END
            WHERE status IN ('running', 'error', 'too_short')
            """
        )
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "browser_history_summaries",
        _status_check(LEGACY_STATES),
    )
