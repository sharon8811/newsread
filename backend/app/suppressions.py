"""Materialize "not interested" rules into article_suppressions rows.

Both matchers are single set-based INSERT ... SELECT ... ON CONFLICT DO
NOTHING statements — no per-article model calls, ever. The worker re-scans a
short trailing window every cycle (idempotent, and self-healing for articles
whose embedding lands a cycle late); rule creation runs the same statements
over a longer window as a backfill, whose matches double as the "also hid N
recent articles" preview. The vector leg inner-joins article_embeddings, so
articles without an embedding are never suppressed (fail-open by structure).
"""

from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import db
from .models import (
    Article,
    ArticleEmbedding,
    ArticleEntity,
    ArticleSuppression,
    DislikeRuleEmbedding,
    Subscription,
    UserDislikeRule,
)

# Worker window: generous vs the 3-minute poll cron so late embeddings still
# get matched; ON CONFLICT keeps the repeated scans free of duplicates.
SUPPRESS_WINDOW = timedelta(days=2)
# Rule-creation backfill: far enough back to clean the visible feed.
BACKFILL_WINDOW = timedelta(days=28)

_INSERT_COLUMNS = ["user_id", "article_id", "rule_id"]


def _scoped(stmt, *, cutoff, feed_id, rule_id):
    """Common filters: recency, optional single feed, optional single rule,
    and only feeds the rule's owner is subscribed to (keeps the table from
    accumulating rows for articles the user could never see)."""
    stmt = stmt.join(
        Subscription,
        and_(
            Subscription.user_id == UserDislikeRule.user_id,
            Subscription.feed_id == Article.feed_id,
        ),
    ).where(Article.fetched_at >= cutoff)
    if feed_id is not None:
        stmt = stmt.where(Article.feed_id == feed_id)
    if rule_id is not None:
        stmt = stmt.where(UserDislikeRule.id == rule_id)
    return stmt


async def apply_entity_rules(
    session: AsyncSession,
    *,
    cutoff,
    feed_id: int | None = None,
    rule_id: int | None = None,
) -> int:
    select_stmt = _scoped(
        select(UserDislikeRule.user_id, ArticleEntity.article_id, UserDislikeRule.id)
        .select_from(UserDislikeRule)
        .join(ArticleEntity, ArticleEntity.entity_id == UserDislikeRule.entity_id)
        .join(Article, Article.id == ArticleEntity.article_id)
        .where(UserDislikeRule.kind == "entity"),
        cutoff=cutoff,
        feed_id=feed_id,
        rule_id=rule_id,
    )
    stmt = (
        pg_insert(ArticleSuppression)
        .from_select(_INSERT_COLUMNS, select_stmt)
        .on_conflict_do_nothing()
    )
    return (await session.execute(stmt)).rowcount or 0


async def apply_vector_rules(
    session: AsyncSession,
    *,
    cutoff,
    feed_id: int | None = None,
    rule_id: int | None = None,
) -> int:
    if not db.vector_enabled:
        return 0
    select_stmt = _scoped(
        select(UserDislikeRule.user_id, ArticleEmbedding.article_id, UserDislikeRule.id)
        .select_from(UserDislikeRule)
        .join(DislikeRuleEmbedding, DislikeRuleEmbedding.rule_id == UserDislikeRule.id)
        # Same-model only: vector dimensions differ across embedding models.
        .join(ArticleEmbedding, ArticleEmbedding.model == DislikeRuleEmbedding.model)
        .join(Article, Article.id == ArticleEmbedding.article_id)
        .where(
            UserDislikeRule.kind.in_(("topic", "story")),
            or_(UserDislikeRule.expires_at.is_(None), UserDislikeRule.expires_at > func.now()),
            ArticleEmbedding.embedding.cosine_distance(DislikeRuleEmbedding.embedding)
            < UserDislikeRule.threshold,
        ),
        cutoff=cutoff,
        feed_id=feed_id,
        rule_id=rule_id,
    )
    stmt = (
        pg_insert(ArticleSuppression)
        .from_select(_INSERT_COLUMNS, select_stmt)
        .on_conflict_do_nothing()
    )
    return (await session.execute(stmt)).rowcount or 0
