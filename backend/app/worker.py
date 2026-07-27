"""ARQ worker: polls feeds, enriches articles (full text + og:image), and
auto-generates the three summary levels for new articles.

Run with: arq app.worker.WorkerSettings
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import selectinload

from . import (
    catalog_embeddings,
    db,
    embeddings,
    history_embeddings,
    history_gc,
    history_operations,
    history_summaries,
    llm,
    ner,
    push,
    queue,
    suppressions,
)
from .config import settings
from .db import init_db
from .enrichers.pipeline import extract_entities, refresh_stale_entities
from .extractor import SUMMARIZABLE_FEED_HTML_CHARS, enrich_article
from .fetcher import refresh_feed
from .history_ingest import get_history_ingest_service
from .models import (
    Article,
    ArticleEmbedding,
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryPageDocument,
    Feed,
    Project,
    ProjectArticle,
    ProjectArticleComment,
    Share,
    UserDislikeRule,
)
from .summarizer import (
    SummarySkipped,
    ThinContentError,
    generate_summaries,
    transcript_still_owed,
)

logger = logging.getLogger(__name__)

ENRICH_BATCH = 20
SUMMARIZE_BATCH = 10
# How many chained enrich_feed passes one subscribe may trigger. Five passes
# clear a 100-entry feed; past that the 3-minutely poll can take over rather
# than let one feed hold the queue.
MAX_ENRICH_PASSES = 5
EMBED_BATCH = 50
HISTORY_EMBED_BATCH = 50
NER_BATCH = 10
ENRICH_CONCURRENCY = 4
# One LLM request per article (llm.summarize parses all three levels from a
# single completion), so this is exactly how many requests are in flight
# against the model endpoint. Kept at/below SUMMARIZE_BATCH: a higher value
# cannot help, there are only that many articles in a cycle.
SUMMARIZE_CONCURRENCY = 8
NER_CONCURRENCY = 2

# Stage gates are module-level on purpose. arq runs every job in one event loop
# in a single process, so one semaphore per stage bounds concurrency across
# *overlapping* jobs — the poll_feeds cron racing queued enrich_feed calls, or
# several enrich_feed jobs from a bulk feed import (arq runs up to max_jobs at
# once). A semaphore created per invocation only bounds that invocation, so N
# concurrent jobs multiply to N x limit connection checkouts and overrun the
# engine pool; _for_each_article holds a session per article for the whole call,
# which for summarization can reach the 120s LLM timeout. Total ceiling here is
# ENRICH + SUMMARIZE + NER = 14, which db.py's pool is sized to absorb.
_ENRICH_GATE = asyncio.Semaphore(ENRICH_CONCURRENCY)
_SUMMARIZE_GATE = asyncio.Semaphore(SUMMARIZE_CONCURRENCY)
_NER_GATE = asyncio.Semaphore(NER_CONCURRENCY)


async def _for_each_article(ids, *, gate: asyncio.Semaphore, label: str, fn) -> None:
    """Run fn(session, article) for each id, each in its own session, at most
    `gate`'s limit at a time across every job in this worker. Failures are
    logged per article and never stop the batch; fn owns any transaction
    discipline beyond that."""

    async def one(article_id: int) -> None:
        async with gate:
            # The whole session block is guarded, not just fn: acquiring a
            # connection can itself fail (pool timeout under load), and that
            # must degrade one article rather than kill the job and skip the
            # pipeline stages that follow.
            try:
                async with db.SessionLocal() as session:
                    article = await session.get(Article, article_id)
                    if article is None:
                        return
                    await fn(session, article)
            except Exception as exc:
                logger.warning("%s of article %s failed: %s", label, article_id, exc)

    # No return_exceptions: `one` already swallows Exception, and letting
    # BaseException (worker shutdown's CancelledError) propagate is correct.
    await asyncio.gather(*(one(article_id) for article_id in ids))


async def _summarize_quietly(session, article) -> None:
    if transcript_still_owed(article):
        # The enrich stage left this video pending because YouTube refused the
        # caption request. generate_summaries would refuse it too, but as a
        # ThinContentError — which this function records as needs_full_page,
        # permanently excluding an article whose captions are merely delayed.
        return
    try:
        await generate_summaries(session, article, allow_refetch=False)
    except SummarySkipped:
        pass  # already stamped summary_skipped_reason
    except ThinContentError:
        # No usable text, and the batch path spends neither a browser render
        # nor vision tokens to get some. Record it so the worker stops
        # retrying this article every cycle and the feed's pending count can
        # reach zero. The detail view still summarizes it on demand, where a
        # refetch and vision are allowed — only "too_short" suppresses that.
        article.summary_skipped_reason = "needs_full_page"
        await session.commit()


async def enrich_and_summarize(ctx: dict | None = None, feed_id: int | None = None) -> bool:
    """Fill missing full text / images, then summaries, newest articles first.

    Returns True when a stage ran a full batch, i.e. there is probably more of
    this feed still to do.
    """
    async with db.SessionLocal() as session:
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

    await _for_each_article(enrich_ids, gate=_ENRICH_GATE, label="Enrichment", fn=enrich_article)

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
            logger.info(
                "Enriched %d articles, extracted entities for %d", len(enrich_ids), extracted
            )
        return len(enrich_ids) >= ENRICH_BATCH

    async with db.SessionLocal() as session:
        summarize_query = (
            select(Article.id)
            .join(Feed, Feed.id == Article.feed_id)
            .where(Feed.ai_enabled.is_(True))
            .where(Article.summary_short == "")
            .where(Article.summary_skipped_reason.is_(None))
            # Skip articles whose page fetch already failed and whose feed
            # content is a stub — they'd be ThinContent-skipped every cycle.
            .where(
                or_(
                    Article.full_text != "",
                    Article.full_text_fetched_at.is_(None),
                    func.length(Article.content_html) > SUMMARIZABLE_FEED_HTML_CHARS,
                )
            )
            .order_by(Article.id.desc())
            .limit(SUMMARIZE_BATCH)
        )
        if feed_id is not None:
            summarize_query = summarize_query.where(Article.feed_id == feed_id)
        summarize_ids = list(await session.scalars(summarize_query))

    await _for_each_article(
        summarize_ids,
        gate=_SUMMARIZE_GATE,
        label="Auto-summary",
        fn=_summarize_quietly,
    )

    tagged = await extract_named_entities_batch(feed_id=feed_id)
    embedded = await embed_articles_batch(feed_id=feed_id)
    history_documents_embedded = await embed_history_documents_batch()
    suppressed = await suppress_articles_batch(feed_id=feed_id)

    if (
        enrich_ids
        or summarize_ids
        or tagged
        or embedded
        or history_documents_embedded
        or suppressed
    ):
        logger.info(
            "Enriched %d articles, summarized up to %d, tagged %d, "
            "embedded %d articles and %d history documents, suppressed %d",
            len(enrich_ids),
            len(summarize_ids),
            tagged,
            embedded,
            history_documents_embedded,
            suppressed,
        )
    return len(enrich_ids) >= ENRICH_BATCH or len(summarize_ids) >= SUMMARIZE_BATCH


async def _ner_one(session, article) -> None:
    try:
        await ner.extract_named(session, article)
    except Exception as exc:
        logger.warning("Entity tagging of article %s failed: %s", article.id, exc)
        await session.rollback()
    # Always stamp: never re-tag on failure, never block the cycle.
    article.ner_extracted_at = datetime.now(UTC)
    await session.commit()


async def extract_named_entities_batch(feed_id: int | None = None) -> int:
    """LLM named-entity tagging for articles that have been enriched or
    summarized (the extraction reads ner.body_for). Articles first tagged
    from a title-only body are re-tagged when their summary lands later —
    the stamp comparison converges the same way the entity link rescan
    does. Returns how many were processed."""
    if not llm.is_configured():
        return 0
    async with db.SessionLocal() as session:
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
    await _for_each_article(ids, gate=_NER_GATE, label="Entity tagging", fn=_ner_one)
    return len(ids)


async def embed_articles_batch(feed_id: int | None = None) -> int:
    """Embed articles that have no vector yet, one from a different model
    (e.g. after an OPENAI_EMBEDDING_MODEL switch), or one embedded from text
    the article no longer has (embeddings.stale_input), newest first. Runs
    after the summarize stage so fresh articles usually embed their summary."""
    if not embeddings.is_configured():
        return 0
    async with db.SessionLocal() as session:
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


async def embed_history_document(ctx: dict | None, document_id: int) -> int:
    """Embed one newly linked document; cron retries transient failures."""
    try:
        return await history_embeddings.embed_document(document_id)
    except Exception as exc:
        logger.warning("History document %s embedding failed: %s", document_id, exc)
        return 0


async def embed_history_documents_batch() -> int:
    """Embed a bounded batch of linked, missing/stale v2 documents."""
    if not history_embeddings.is_configured():
        return 0
    async with db.SessionLocal() as session:
        current_embedding = (
            select(BrowserHistoryDocumentEmbedding.id)
            .where(
                BrowserHistoryDocumentEmbedding.document_id == BrowserHistoryDocument.id,
                BrowserHistoryDocumentEmbedding.model == settings.openai_embedding_model,
                BrowserHistoryDocumentEmbedding.input_hash == BrowserHistoryDocument.content_hash,
            )
            .exists()
        )
        linked = (
            select(BrowserHistoryPageDocument.id)
            .where(BrowserHistoryPageDocument.document_id == BrowserHistoryDocument.id)
            .exists()
        )
        document_ids = list(
            await session.scalars(
                select(BrowserHistoryDocument.id)
                .where(
                    BrowserHistoryDocument.storage_status == "ready",
                    BrowserHistoryDocument.extraction_version
                    == history_embeddings.DOCUMENT_EXTRACTION_VERSION,
                    linked,
                    ~current_embedding,
                )
                .order_by(BrowserHistoryDocument.id.desc())
                .limit(HISTORY_EMBED_BATCH)
            )
        )

    embedded = 0
    for document_id in document_ids:
        try:
            embedded += await history_embeddings.embed_document(document_id)
        except Exception as exc:
            logger.warning("History document %s catch-up failed: %s", document_id, exc)
    return embedded


async def cleanup_history_retention(
    ctx: dict | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Delete expired history pages and version links, repairing current versions."""
    async with db.SessionLocal() as session:
        result = await history_gc.apply_history_retention(session, now=now)
    if result.pages_deleted or result.version_links_deleted:
        logger.info(
            "History retention deleted %d pages and %d version links",
            result.pages_deleted,
            result.version_links_deleted,
        )
    return result.pages_deleted


async def cleanup_history_objects(
    ctx: dict | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Collect unreferenced rows, deletion-outbox work, and orphaned objects."""
    storage = (
        get_history_ingest_service().storage if settings.browser_history_content_enabled else None
    )
    async with db.SessionLocal() as session:
        result = await history_gc.collect_history_garbage(
            session,
            storage=storage,
            now=now,
        )
    total = (
        result.documents_deleted
        + result.images_deleted
        + result.outbox_completed
        + result.orphan_objects_deleted
    )
    if total:
        logger.info(
            "History GC deleted %d document rows, %d image rows, "
            "%d queued objects, and %d orphan objects",
            result.documents_deleted,
            result.images_deleted,
            result.outbox_completed,
            result.orphan_objects_deleted,
        )
    return total


async def audit_history_operations(ctx: dict | None = None) -> int:
    """Emit aggregate pipeline metrics and warnings for operator alerting."""
    now = datetime.now(UTC)
    async with db.SessionLocal() as session:
        metrics = await history_operations.operator_history_operations(session)
    logger.info(
        "History operations: stored_bytes=%d embedding_backlog=%d "
        "deletion_backlog=%d users_near_quota=%d",
        metrics.stored_bytes,
        metrics.embedding_backlog_count,
        metrics.deletion_backlog_count,
        metrics.users_near_storage_quota,
    )
    alerts = 0
    if (
        metrics.embedding_backlog_oldest_at is not None
        and now - metrics.embedding_backlog_oldest_at
        >= timedelta(hours=settings.history_embedding_backlog_alert_hours)
    ):
        alerts += 1
        logger.warning(
            "History embedding backlog is stale: count=%d oldest=%s",
            metrics.embedding_backlog_count,
            metrics.embedding_backlog_oldest_at.isoformat(),
        )
    if (
        metrics.deletion_backlog_oldest_at is not None
        and now - metrics.deletion_backlog_oldest_at
        >= timedelta(hours=settings.history_deletion_backlog_alert_hours)
    ):
        alerts += 1
        logger.warning(
            "History object-deletion backlog is stale: count=%d oldest=%s",
            metrics.deletion_backlog_count,
            metrics.deletion_backlog_oldest_at.isoformat(),
        )
    if metrics.users_near_storage_quota:
        alerts += 1
        logger.warning(
            "%d history users are at or above %.0f%% of storage quota",
            metrics.users_near_storage_quota,
            settings.history_storage_alert_ratio * 100,
        )
    return alerts


async def suppress_articles_batch(feed_id: int | None = None) -> int:
    """Materialize dislike rules over recently fetched articles (pure SQL, no
    model calls — the reason suppression can run ahead of every consumer).
    Failures are swallowed: a missed cycle self-heals inside SUPPRESS_WINDOW."""
    async with db.SessionLocal() as session:
        try:
            # Expired story mutes delete themselves; the FK cascade frees
            # their suppressions, so the muted articles quietly reappear.
            await session.execute(
                delete(UserDislikeRule).where(
                    UserDislikeRule.expires_at.isnot(None),
                    UserDislikeRule.expires_at <= func.now(),
                )
            )
            cutoff = datetime.now(UTC) - suppressions.SUPPRESS_WINDOW
            count = await suppressions.apply_entity_rules(session, cutoff=cutoff, feed_id=feed_id)
            count += await suppressions.apply_vector_rules(session, cutoff=cutoff, feed_id=feed_id)
            await session.commit()
            return count
        except Exception as exc:
            logger.warning("Suppression stage failed: %s", exc)
            return 0


async def enrich_feed(ctx: dict, feed_id: int, pass_number: int = 1) -> None:
    """Enqueued by the API right after a feed is added.

    A typical feed carries more entries than SUMMARIZE_BATCH, so one pass
    leaves a freshly subscribed feed half-summarized. Chain another pass
    rather than making the new subscriber wait out the 3-minutely poll — as
    separate jobs, so the stage gates still bound concurrency and a long feed
    never monopolizes a worker slot.
    """
    more = await enrich_and_summarize(ctx, feed_id=feed_id)
    if not more or pass_number >= MAX_ENRICH_PASSES:
        return
    redis = (ctx or {}).get("redis")
    if redis is None:
        await queue.enqueue("enrich_feed", feed_id, pass_number + 1)
    else:
        await redis.enqueue_job("enrich_feed", feed_id, pass_number + 1)


async def send_share_push(ctx: dict, share_id: int) -> None:
    """Enqueued by the API when a share is created; notifies each recipient's
    registered mobile devices."""
    async with db.SessionLocal() as session:
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
    async with db.SessionLocal() as session:
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
                .order_by(ProjectArticleComment.created_at.desc(), ProjectArticleComment.id.desc())
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
        async with db.SessionLocal() as session:
            count = await catalog_embeddings.embed_catalog_batch(session)
        total += count
        if count == 0:
            break
    if total:
        logger.info("Embedded %d new or changed catalog entries", total)


async def poll_feeds(ctx: dict) -> None:
    now = datetime.now(UTC)
    async with db.SessionLocal() as session:
        # Hidden per-user import feeds carry newsread:// sentinel URLs — there
        # is nothing to poll; their articles arrive via POST /imports.
        feeds = (await session.scalars(select(Feed).where(Feed.owner_user_id.is_(None)))).all()
        for feed in feeds:
            interval = timedelta(
                minutes=feed.refresh_interval_minutes or settings.feed_refresh_minutes
            )
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
    functions = [
        enrich_feed,
        embed_history_document,
        history_summaries.generate_history_summary,
        send_share_push,
        send_project_pin_push,
    ]
    cron_jobs = [
        cron(poll_feeds, minute=set(range(0, 60, 3)), run_at_startup=True),
        cron(refresh_entities, minute={7, 37}),
        cron(refresh_catalog_embeddings, minute=17, run_at_startup=True),
        cron(history_summaries.generate_history_summaries_batch, minute=None),
        cron(cleanup_history_objects, minute=set(range(0, 60, 10))),
        cron(audit_history_operations, minute={5, 20, 35, 50}),
        cron(cleanup_history_retention, hour=3, minute=11),
    ]
