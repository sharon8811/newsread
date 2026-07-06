"""ARQ worker: polls feeds, enriches articles (full text + og:image), and
auto-generates the three summary levels for new articles.

Run with: arq app.worker.WorkerSettings
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, or_, select

from . import embeddings, llm
from .config import settings
from .db import SessionLocal, init_db
from .enrichers.pipeline import extract_entities, refresh_stale_entities
from .extractor import enrich_article
from .fetcher import refresh_feed
from .models import Article, ArticleEmbedding, Feed
from .summarizer import ThinContentError, generate_summaries

logger = logging.getLogger(__name__)

ENRICH_BATCH = 20
SUMMARIZE_BATCH = 10
EMBED_BATCH = 50
ENRICH_CONCURRENCY = 4
SUMMARIZE_CONCURRENCY = 2


async def _enrich_one(article_id: int, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        async with SessionLocal() as session:
            article = await session.get(Article, article_id)
            if article is None:
                return
            try:
                await enrich_article(session, article)
            except Exception as exc:
                logger.warning("Enrichment of article %s failed: %s", article_id, exc)


async def _summarize_one(article_id: int, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        async with SessionLocal() as session:
            article = await session.get(Article, article_id)
            if article is None:
                return
            try:
                await generate_summaries(session, article, allow_refetch=False)
            except ThinContentError:
                pass  # site blocks fetching; the article view explains this
            except Exception as exc:
                logger.warning("Auto-summary of article %s failed: %s", article_id, exc)


async def enrich_and_summarize(ctx: dict | None = None, feed_id: int | None = None) -> None:
    """Fill missing full text / images, then summaries, newest articles first."""
    async with SessionLocal() as session:
        enrich_query = (
            select(Article.id)
            .where(or_(Article.full_text == "", Article.image_url.is_(None)))
            .where(Article.full_text_fetched_at.is_(None))
            .order_by(Article.id.desc())
            .limit(ENRICH_BATCH)
        )
        if feed_id is not None:
            enrich_query = enrich_query.where(Article.feed_id == feed_id)
        enrich_ids = list(await session.scalars(enrich_query))

    semaphore = asyncio.Semaphore(ENRICH_CONCURRENCY)
    await asyncio.gather(*(_enrich_one(aid, semaphore) for aid in enrich_ids))

    try:
        extracted = await extract_entities(feed_id=feed_id)
    except Exception as exc:
        extracted = 0
        logger.warning("Entity extraction stage failed: %s", exc)

    if not llm.is_configured():
        if enrich_ids or extracted:
            logger.info("Enriched %d articles, extracted entities for %d", len(enrich_ids), extracted)
        return

    async with SessionLocal() as session:
        summarize_query = (
            select(Article.id)
            .join(Feed, Feed.id == Article.feed_id)
            .where(Feed.ai_enabled.is_(True))
            .where(Article.summary_short == "")
            # Skip articles whose page fetch already failed and whose feed
            # content is a stub — they'd be ThinContent-skipped every cycle.
            .where(
                or_(
                    Article.full_text != "",
                    Article.full_text_fetched_at.is_(None),
                    func.length(Article.content_html) > 1600,
                )
            )
            .order_by(Article.id.desc())
            .limit(SUMMARIZE_BATCH)
        )
        if feed_id is not None:
            summarize_query = summarize_query.where(Article.feed_id == feed_id)
        summarize_ids = list(await session.scalars(summarize_query))

    semaphore = asyncio.Semaphore(SUMMARIZE_CONCURRENCY)
    await asyncio.gather(*(_summarize_one(aid, semaphore) for aid in summarize_ids))

    embedded = await embed_articles_batch(feed_id=feed_id)

    if enrich_ids or summarize_ids or embedded:
        logger.info(
            "Enriched %d articles, summarized up to %d, embedded %d",
            len(enrich_ids),
            len(summarize_ids),
            embedded,
        )


async def embed_articles_batch(feed_id: int | None = None) -> int:
    """Embed articles that have no vector yet (or one from a different model,
    e.g. after an OPENAI_EMBEDDING_MODEL switch), newest first. Runs after the
    summarize stage so fresh articles usually embed their summary."""
    if not embeddings.is_configured():
        return 0
    async with SessionLocal() as session:
        embed_query = (
            select(Article)
            .join(Feed, Feed.id == Article.feed_id)
            .outerjoin(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
            .where(Feed.ai_enabled.is_(True))
            .where(
                or_(
                    ArticleEmbedding.article_id.is_(None),
                    ArticleEmbedding.model != settings.openai_embedding_model,
                )
            )
            .order_by(Article.id.desc())
            .limit(EMBED_BATCH)
        )
        if feed_id is not None:
            embed_query = embed_query.where(Article.feed_id == feed_id)
        articles = (await session.scalars(embed_query)).all()
        try:
            return await embeddings.embed_articles(session, list(articles))
        except Exception as exc:
            logger.warning("Embedding stage failed: %s", exc)
            return 0


async def enrich_feed(ctx: dict, feed_id: int) -> None:
    """Enqueued by the API right after a feed is added."""
    await enrich_and_summarize(ctx, feed_id=feed_id)


async def refresh_entities(ctx: dict) -> None:
    try:
        await refresh_stale_entities()
    except Exception as exc:
        logger.warning("Entity refresh failed: %s", exc)


async def poll_feeds(ctx: dict) -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        feeds = (await session.scalars(select(Feed))).all()
        for feed in feeds:
            interval = timedelta(minutes=feed.refresh_interval_minutes or settings.feed_refresh_minutes)
            due = feed.last_fetched_at is None or feed.last_fetched_at + interval <= now
            if not due:
                continue
            try:
                await refresh_feed(session, feed)
            except Exception as exc:
                logger.warning("Polling feed %s failed: %s", feed.url, exc)
                await session.rollback()
    await enrich_and_summarize(ctx)


async def startup(ctx: dict) -> None:
    await init_db()
    logger.info("Feed worker started (LLM configured: %s)", llm.is_configured())


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    functions = [enrich_feed]
    cron_jobs = [
        cron(poll_feeds, minute=set(range(0, 60, 3)), run_at_startup=True),
        cron(refresh_entities, minute={7, 37}),
    ]
