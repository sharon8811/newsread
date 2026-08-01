"""Recording article-pipeline outcomes for the admin processing-health trends.

Failures and skips get an event row because nothing else dates them; successes
are already dated by the articles' own columns (fetched_at,
full_text_fetched_at, summary_generated_at). `detail` must stay a short code
or exception class name — never error text, which may carry page or user
content into analytics.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from . import db
from .models import ArticleProcessingEvent

logger = logging.getLogger(__name__)

STAGE_POLL = "poll"
STAGE_ENRICH = "enrich"
STAGE_SUMMARIZE = "summarize"
STAGE_NER = "ner"
STAGE_IMPORT = "import"

OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"


def add_event(
    session: AsyncSession,
    *,
    stage: str,
    outcome: str,
    article_id: int | None = None,
    feed_id: int | None = None,
    detail: str = "",
) -> None:
    """Stage an event on the caller's session; their commit carries it."""
    session.add(
        ArticleProcessingEvent(
            article_id=article_id,
            feed_id=feed_id,
            stage=stage,
            outcome=outcome,
            detail=detail[:120],
        )
    )


async def record_event(
    *,
    stage: str,
    outcome: str,
    article_id: int | None = None,
    feed_id: int | None = None,
    detail: str = "",
) -> None:
    """Write one event on its own session — for failure paths whose original
    session/transaction is already dead. Never raises."""
    try:
        async with db.SessionLocal() as session:
            add_event(
                session,
                stage=stage,
                outcome=outcome,
                article_id=article_id,
                feed_id=feed_id,
                detail=detail,
            )
            await session.commit()
    except Exception as exc:
        logger.warning("Recording %s/%s processing event failed: %s", stage, outcome, exc)
