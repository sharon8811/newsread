"""Worker-side entity extraction and refresh.

Failures never block the article pipeline: articles are always stamped
`entities_extracted_at`, and a failed entity fetch degrades to "link the
stale row or skip".
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..fetcher import USER_AGENT
from ..models import Article, ArticleEntity, Entity, EntitySnapshot
from . import BY_KIND, ENRICHERS, Enricher, EnrichError, extract_links, extract_text_links, match_url

logger = logging.getLogger(__name__)

ENTITY_BATCH = 20
ENTITY_CONCURRENCY = 4
REFRESH_BATCH = 30
MAX_ENTITIES_PER_ARTICLE = 5
# Refresh entities only while some linking article is reasonably fresh.
REFRESH_ARTICLE_WINDOW = timedelta(days=14)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=10,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        limits=httpx.Limits(max_connections=8),
    )


async def _get_or_refresh(
    session: AsyncSession,
    enricher: Enricher,
    key: str,
    client: httpx.AsyncClient,
) -> Entity | None:
    now = datetime.now(timezone.utc)
    entity = await session.scalar(
        select(Entity).where(Entity.kind == enricher.kind, Entity.canonical_key == key)
    )
    if entity is not None and entity.fetched_at is not None:
        if now - entity.fetched_at < enricher.ttl and entity.data:
            return entity

    try:
        data = await enricher.fetch(key, client)
    except EnrichError as exc:
        logger.debug("Enrich %s:%s skipped: %s", enricher.kind, key, exc)
        return entity  # stale row if it exists, else None
    except Exception as exc:
        logger.warning("Enrich %s:%s failed: %s", enricher.kind, key, exc)
        return entity

    result = await session.execute(
        pg_insert(Entity)
        .values(
            kind=enricher.kind,
            canonical_key=key,
            url=enricher.entity_url(key)[:2048],
            data=data,
            fetched_at=now,
        )
        .on_conflict_do_update(
            index_elements=["kind", "canonical_key"],
            set_={"data": data, "fetched_at": now},
        )
        .returning(Entity.id)
    )
    entity_id = result.scalar_one()

    # Snapshot on first fetch and whenever the payload actually changed;
    # "value at time T" is then the last snapshot with captured_at <= T.
    changed = entity is None or entity.data != data
    if changed:
        session.add(EntitySnapshot(entity_id=entity_id, data=data))
    await session.flush()
    return await session.get(Entity, entity_id)


async def link_article_entities(
    session: AsyncSession,
    article: Article,
    client: httpx.AsyncClient,
    locks: dict[tuple[str, str], asyncio.Lock],
) -> int:
    candidates: list[tuple[str, str]] = [(article.url, "primary")]
    if article.comments_url:
        candidates.append((article.comments_url, "inline"))
    candidates += [(href, "inline") for href in extract_links(article.content_html)]
    # The fetched body often carries links the feed stub lacks entirely (HN
    # feeds ship no content); full_text is extracted prose, so bare URLs are
    # all that survive of its anchors.
    candidates += [(href, "inline") for href in extract_text_links(article.full_text)]

    linked: set[tuple[str, str]] = set()
    position = 0
    for raw, source in candidates:
        if position >= MAX_ENTITIES_PER_ARTICLE:
            break
        matched = match_url(raw)
        if matched is None:
            continue
        enricher, key = matched
        if (enricher.kind, key) in linked:
            continue
        async with locks[(enricher.kind, key)]:
            entity = await _get_or_refresh(session, enricher, key, client)
        if entity is None:
            continue
        await session.execute(
            pg_insert(ArticleEntity)
            .values(article_id=article.id, entity_id=entity.id, source=source, position=position)
            .on_conflict_do_nothing(index_elements=["article_id", "entity_id"])
        )
        linked.add((enricher.kind, key))
        position += 1
    return position


async def _extract_one(
    article_id: int,
    semaphore: asyncio.Semaphore,
    client: httpx.AsyncClient,
    locks: dict[tuple[str, str], asyncio.Lock],
) -> None:
    async with semaphore:
        async with SessionLocal() as session:
            article = await session.get(Article, article_id)
            if article is None:
                return
            try:
                await link_article_entities(session, article, client, locks)
            except Exception as exc:
                logger.warning("Entity extraction for article %s failed: %s", article_id, exc)
            # Always stamp, even on failure — never rescan, never block.
            article.entities_extracted_at = datetime.now(timezone.utc)
            await session.commit()


async def extract_entities(feed_id: int | None = None) -> int:
    """Extract + link entities for articles not yet scanned — or scanned
    before their full text arrived, whose body links were invisible then.
    Rescans converge because the new stamp postdates full_text_fetched_at.
    Returns count."""
    async with SessionLocal() as session:
        query = (
            select(Article.id)
            .where(
                or_(
                    Article.entities_extracted_at.is_(None),
                    and_(
                        Article.full_text_fetched_at.is_not(None),
                        Article.entities_extracted_at < Article.full_text_fetched_at,
                    ),
                )
            )
            .order_by(Article.id.desc())
            .limit(ENTITY_BATCH)
        )
        if feed_id is not None:
            query = query.where(Article.feed_id == feed_id)
        ids = list(await session.scalars(query))
    if not ids:
        return 0

    semaphore = asyncio.Semaphore(ENTITY_CONCURRENCY)
    locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
    async with _make_client() as client:
        await asyncio.gather(*(_extract_one(aid, semaphore, client, locks) for aid in ids))
    return len(ids)


async def refresh_stale_entities() -> int:
    """Refetch entities past their TTL that fresh articles still reference."""
    now = datetime.now(timezone.utc)
    stale: list[tuple[int, str, str]] = []
    async with SessionLocal() as session:
        for enricher in ENRICHERS:
            rows = await session.execute(
                select(Entity.id, Entity.kind, Entity.canonical_key)
                .where(
                    Entity.kind == enricher.kind,
                    Entity.fetched_at < now - enricher.ttl,
                    exists(
                        select(1)
                        .select_from(ArticleEntity)
                        .join(Article, Article.id == ArticleEntity.article_id)
                        .where(
                            ArticleEntity.entity_id == Entity.id,
                            Article.published_at > now - REFRESH_ARTICLE_WINDOW,
                        )
                    ),
                )
                .order_by(Entity.fetched_at.asc())
                .limit(REFRESH_BATCH - len(stale))
            )
            stale.extend(rows.all())
            if len(stale) >= REFRESH_BATCH:
                break
    if not stale:
        return 0

    semaphore = asyncio.Semaphore(ENTITY_CONCURRENCY)

    async def _refresh_one(kind: str, key: str) -> None:
        async with semaphore:
            async with SessionLocal() as session:
                try:
                    await _get_or_refresh(session, BY_KIND[kind], key, client)
                    await session.commit()
                except Exception as exc:
                    logger.warning("Entity refresh %s:%s failed: %s", kind, key, exc)

    async with _make_client() as client:
        await asyncio.gather(*(_refresh_one(kind, key) for _, kind, key in stale))
    logger.info("Refreshed %d stale entities", len(stale))
    return len(stale)
