"""ARQ worker: polls feeds, enriches articles (full text + og:image), and
auto-generates the three summary levels for new articles.

Run with: arq app.worker.WorkerSettings
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import selectinload

from . import catalog_embeddings, embeddings, llm, ner, push, suppressions
from .config import settings
from .db import SessionLocal, init_db
from .enrichers.pipeline import extract_entities, refresh_stale_entities
from .extractor import enrich_article
from .fetcher import refresh_feed
from .models import (
    Article,
    ArticleEmbedding,
    Feed,
    Project,
    ProjectArticle,
    ProjectArticleComment,
    Share,
    UserDislikeRule,
)
from .summarizer import ThinContentError, generate_summaries

logger = logging.getLogger(__name__)

ENRICH_BATCH = 20
SUMMARIZE_BATCH = 10
EMBED_BATCH = 50
NER_BATCH = 10
ENRICH_CONCURRENCY = 4
SUMMARIZE_CONCURRENCY = 2
NER_CONCURRENCY = 2


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
        # Entity rules must still materialize on LLM-less installs (the
        # vector leg no-ops without embeddings).
        await suppress_articles_batch(feed_id=feed_id)
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

    tagged = await extract_named_entities_batch(feed_id=feed_id)
    embedded = await embed_articles_batch(feed_id=feed_id)
    suppressed = await suppress_articles_batch(feed_id=feed_id)

    if enrich_ids or summarize_ids or tagged or embedded or suppressed:
        logger.info(
            "Enriched %d articles, summarized up to %d, tagged %d, embedded %d, suppressed %d",
            len(enrich_ids),
            len(summarize_ids),
            tagged,
            embedded,
            suppressed,
        )


async def _ner_one(article_id: int, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        async with SessionLocal() as session:
            article = await session.get(Article, article_id)
            if article is None:
                return
            try:
                await ner.extract_named(session, article)
            except Exception as exc:
                logger.warning("Entity tagging of article %s failed: %s", article_id, exc)
                await session.rollback()
            # Always stamp: never re-tag on failure, never block the cycle.
            article.ner_extracted_at = datetime.now(timezone.utc)
            await session.commit()


async def extract_named_entities_batch(feed_id: int | None = None) -> int:
    """LLM named-entity tagging for articles that have been enriched or
    summarized (the extraction reads ner.body_for). Articles first tagged
    from a title-only body are re-tagged when their summary lands later —
    the stamp comparison converges the same way the entity link rescan
    does. Returns how many were processed."""
    if not llm.is_configured():
        return 0
    async with SessionLocal() as session:
        query = (
            select(Article.id)
            .join(Feed, Feed.id == Article.feed_id)
            .where(Feed.ai_enabled.is_(True))
            .where(
                or_(
                    Article.ner_extracted_at.is_(None),
                    and_(
                        Article.summary_generated_at.is_not(None),
                        Article.ner_extracted_at < Article.summary_generated_at,
                    ),
                )
            )
            .where(
                or_(
                    Article.full_text_fetched_at.is_not(None),
                    Article.summary_medium != "",
                )
            )
            .order_by(Article.id.desc())
            .limit(NER_BATCH)
        )
        if feed_id is not None:
            query = query.where(Article.feed_id == feed_id)
        ids = list(await session.scalars(query))
    if not ids:
        return 0
    semaphore = asyncio.Semaphore(NER_CONCURRENCY)
    await asyncio.gather(*(_ner_one(aid, semaphore) for aid in ids))
    return len(ids)


async def embed_articles_batch(feed_id: int | None = None) -> int:
    """Embed articles that have no vector yet, one from a different model
    (e.g. after an OPENAI_EMBEDDING_MODEL switch), or one embedded from text
    the article no longer has (embeddings.stale_input), newest first. Runs
    after the summarize stage so fresh articles usually embed their summary."""
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
                    embeddings.stale_input(),
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


async def suppress_articles_batch(feed_id: int | None = None) -> int:
    """Materialize dislike rules over recently fetched articles (pure SQL, no
    model calls — the reason suppression can run ahead of every consumer).
    Failures are swallowed: a missed cycle self-heals inside SUPPRESS_WINDOW."""
    async with SessionLocal() as session:
        try:
            # Expired story mutes delete themselves; the FK cascade frees
            # their suppressions, so the muted articles quietly reappear.
            await session.execute(
                delete(UserDislikeRule).where(
                    UserDislikeRule.expires_at.isnot(None),
                    UserDislikeRule.expires_at <= func.now(),
                )
            )
            cutoff = datetime.now(timezone.utc) - suppressions.SUPPRESS_WINDOW
            count = await suppressions.apply_entity_rules(session, cutoff=cutoff, feed_id=feed_id)
            count += await suppressions.apply_vector_rules(session, cutoff=cutoff, feed_id=feed_id)
            await session.commit()
            return count
        except Exception as exc:
            logger.warning("Suppression stage failed: %s", exc)
            return 0


async def enrich_feed(ctx: dict, feed_id: int) -> None:
    """Enqueued by the API right after a feed is added."""
    await enrich_and_summarize(ctx, feed_id=feed_id)


async def send_share_push(ctx: dict, share_id: int) -> None:
    """Enqueued by the API when a share is created; notifies each recipient's
    registered mobile devices."""
    async with SessionLocal() as session:
        share = await session.scalar(
            select(Share)
            .where(Share.id == share_id)
            .options(
                selectinload(Share.recipients),
                selectinload(Share.from_user),
                selectinload(Share.article),
            )
        )
    if share is None:
        return
    sent = await push.send_push(
        [r.to_user_id for r in share.recipients],
        title=f"@{share.from_user.username} shared an article",
        body=share.note or share.article.title,
        data={"type": "share", "share_id": share.id, "article_id": share.article_id},
    )
    if sent:
        logger.info("Share %d: sent %d push notifications", share_id, sent)


async def send_project_pin_push(ctx: dict, pin_id: int) -> None:
    """Enqueued when a pin is published to a project; notifies every other
    member's devices, except members who muted the project."""
    async with SessionLocal() as session:
        pin = await session.scalar(
            select(ProjectArticle)
            .where(ProjectArticle.id == pin_id)
            .options(
                selectinload(ProjectArticle.project).selectinload(Project.members),
                selectinload(ProjectArticle.added_by),
                selectinload(ProjectArticle.article),
            )
        )
        note = None
        if pin is not None:
            # The adder's latest thread comment stands in for the old pin note.
            note = await session.scalar(
                select(ProjectArticleComment.body)
                .where(
                    ProjectArticleComment.project_id == pin.project_id,
                    ProjectArticleComment.article_id == pin.article_id,
                    ProjectArticleComment.author_id == pin.added_by_user_id,
                    ProjectArticleComment.body != "",
                )
                .order_by(
                    ProjectArticleComment.created_at.desc(), ProjectArticleComment.id.desc()
                )
                .limit(1)
            )
    if pin is None or not pin.is_shared:
        return  # unpinned or unpublished again before the job ran
    recipients = [
        m.user_id
        for m in pin.project.members
        if m.user_id != pin.added_by_user_id and not m.is_muted
    ]
    sent = await push.send_push(
        recipients,
        title=f"@{pin.added_by.username} · {pin.project.name}",
        body=note or pin.article.title,
        data={
            "type": "project_pin",
            "project_id": pin.project_id,
            "article_id": pin.article_id,
        },
    )
    if sent:
        logger.info("Project pin %d: sent %d push notifications", pin_id, sent)


async def refresh_entities(ctx: dict) -> None:
    try:
        await refresh_stale_entities()
    except Exception as exc:
        logger.warning("Entity refresh failed: %s", exc)


async def refresh_catalog_embeddings(ctx: dict) -> None:
    """Converge the small catalog in one worker pass after seeds change."""
    if not embeddings.is_configured():
        return
    total = 0
    for _ in range(10):
        async with SessionLocal() as session:
            count = await catalog_embeddings.embed_catalog_batch(session)
        total += count
        if count == 0:
            break
    if total:
        logger.info("Embedded %d new or changed catalog entries", total)


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
    functions = [enrich_feed, send_share_push, send_project_pin_push]
    cron_jobs = [
        cron(poll_feeds, minute=set(range(0, 60, 3)), run_at_startup=True),
        cron(refresh_entities, minute={7, 37}),
        cron(refresh_catalog_embeddings, minute=17, run_at_startup=True),
    ]
