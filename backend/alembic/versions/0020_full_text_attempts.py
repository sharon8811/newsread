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
    # Backfill the articles the old fetch-once rule stranded: page fetched, no
    # prose, no summary, no skip reason. One failed attempt is exactly what
    # happened to them, and the count is what the worker's retry leg selects
    # on — left at zero they would never be picked up, and the fix would only
    # ever help articles fetched after the deploy.
    #
    # The content_html bound keeps entries whose feed body IS the article out
    # of it: those have no page fetch behind them to retry, and admitting them
    # would spend a browser render per article for nothing. It is the same
    # raw-length approximation the summarize query already uses, and erring
    # small only leaves a row exactly as this migration found it.
    #
    # The 30-day bound is a cost decision, not a correctness one. On the
    # instance this was written against the unbounded set is 1,689 articles —
    # up to three browser renders each, spaced six hours apart, saturating the
    # enrich stage for days to recover pages nobody is going to open. Inside
    # 30 days it is 1,064, which is where the readers are. Older rows keep
    # their zero and stay exactly as they are today.
    op.execute(
        """
        UPDATE articles SET full_text_attempts = 1
        WHERE full_text = ''
          AND full_text_fetched_at IS NOT NULL
          AND summary = ''
          AND summary_short = ''
          AND summary_skipped_reason IS NULL
          AND length(content_html) <= 1600
          AND fetched_at > now() - interval '30 days'
        """
    )


def downgrade() -> None:
    op.drop_column("articles", "full_text_attempts")
