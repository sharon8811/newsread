import html
import logging
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import embeddings
from ..config import settings
from ..db import get_session
from ..fetcher import FeedParseError, fetch_feed_data, strip_html
from ..models import (
    CatalogEntry,
    CatalogEntryEmbedding,
    CatalogSubmission,
    Feed,
    Subscription,
    User,
)
from ..schemas import (
    CatalogCategoryOut,
    CatalogEntryOut,
    CatalogPreviewItemOut,
    CatalogPreviewOut,
    CatalogSubmissionIn,
    CatalogSubmissionOut,
)
from ..security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"])
SEARCH_POOL = 60
RRF_K = 60
PREVIEW_ITEM_LIMIT = 8
PREVIEW_SUMMARY_CHARS = 240
PREVIEW_TTL_SECONDS = 600
# Repeated modal opens shouldn't re-hit publishers; the catalog is small
# (hundreds of entries), so an unbounded per-process cache is fine.
_preview_cache: dict[int, tuple[float, CatalogPreviewOut]] = {}


def _catalog_filter(category: str | None):
    filters = [CatalogEntry.is_active.is_(True)]
    if category:
        filters.append(CatalogEntry.category == category)
    return filters


async def _hybrid_catalog_ids(
    session: AsyncSession, q: str, category: str | None
) -> tuple[list[int], dict[int, str]] | None:
    """Fuse full-text and semantic candidates with reciprocal rank fusion."""
    tsv = literal_column("catalog_entries.search_tsv")
    tsquery = func.websearch_to_tsquery("english", q)
    keyword_stmt = (
        select(CatalogEntry.id)
        .where(*_catalog_filter(category), tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(tsv, tsquery).desc())
        .limit(SEARCH_POOL)
    )
    keyword_ids = list(await session.scalars(keyword_stmt))
    pattern = f"%{q}%"
    partial_ids = list(await session.scalars(
        select(CatalogEntry.id)
        .where(
            *_catalog_filter(category),
            or_(
                CatalogEntry.title.ilike(pattern),
                CatalogEntry.description.ilike(pattern),
                CatalogEntry.category.ilike(pattern),
                CatalogEntry.url.ilike(pattern),
            ),
        )
        .order_by(func.lower(CatalogEntry.title))
        .limit(SEARCH_POOL)
    ))
    text_ids = list(dict.fromkeys([*keyword_ids, *partial_ids]))
    if len(q) < 3 or not embeddings.is_configured():
        return (text_ids, {entry_id: "Keyword match" for entry_id in text_ids})
    try:
        query_vector = await embeddings.embed_query(q)
    except Exception as exc:
        logger.warning("Catalog query embedding failed, using full-text search: %s", exc)
        return (text_ids, {entry_id: "Keyword match" for entry_id in text_ids})
    vector_ids = list(await session.scalars(
        select(CatalogEntry.id)
        .join(CatalogEntryEmbedding)
        .where(
            *_catalog_filter(category),
            CatalogEntryEmbedding.model == settings.openai_embedding_model,
        )
        .order_by(CatalogEntryEmbedding.embedding.cosine_distance(query_vector))
        .limit(SEARCH_POOL)
    ))
    scores: dict[int, float] = {}
    reasons: dict[int, set[str]] = {}
    for label, leg in (("Semantic match", vector_ids), ("Keyword match", text_ids)):
        for rank, entry_id in enumerate(leg):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (RRF_K + rank + 1)
            reasons.setdefault(entry_id, set()).add(label)
    ranked = sorted(scores, key=lambda entry_id: (-scores[entry_id], -entry_id))
    labels = {
        entry_id: "Keyword and semantic match" if len(reasons[entry_id]) == 2 else next(iter(reasons[entry_id]))
        for entry_id in ranked
    }
    return ranked, labels


async def _recommended_ids(
    session: AsyncSession, user_id: int, category: str | None
) -> list[int]:
    """Rank catalog entries near the centroid of the user's subscriptions."""
    if not embeddings.is_configured():
        return []
    subscribed = list(await session.scalars(
        select(CatalogEntryEmbedding.embedding)
        .join(CatalogEntry, CatalogEntry.id == CatalogEntryEmbedding.catalog_entry_id)
        .join(Feed, Feed.url == CatalogEntry.url)
        .join(Subscription, Subscription.feed_id == Feed.id)
        .where(
            Subscription.user_id == user_id,
            CatalogEntryEmbedding.model == settings.openai_embedding_model,
        )
    ))
    if not subscribed:
        return []
    dimensions = len(subscribed[0])
    centroid = [sum(vector[i] for vector in subscribed) / len(subscribed) for i in range(dimensions)]
    return list(await session.scalars(
        select(CatalogEntry.id)
        .join(CatalogEntryEmbedding)
        .outerjoin(Feed, Feed.url == CatalogEntry.url)
        .outerjoin(
            Subscription,
            and_(Subscription.feed_id == Feed.id, Subscription.user_id == user_id),
        )
        .where(
            *_catalog_filter(category),
            CatalogEntryEmbedding.model == settings.openai_embedding_model,
            Subscription.id.is_(None),
        )
        .order_by(CatalogEntryEmbedding.embedding.cosine_distance(centroid))
    ))


@router.get("", response_model=list[CatalogEntryOut])
async def browse_catalog(
    q: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=64),
    sort: Literal["name", "popular", "recommended"] = "name",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    normalized_q = (q or "").strip()
    ranked_ids: list[int] | None = None
    match_reasons: dict[int, str] = {}
    if normalized_q:
        result = await _hybrid_catalog_ids(session, normalized_q, category)
        if result is not None:
            ranked_ids, match_reasons = result
        # Preserve partial-match behavior for short strings and configurations
        # without embeddings/FTS matches.
        if not ranked_ids:
            pattern = f"%{normalized_q}%"
            ranked_ids = list(await session.scalars(
                select(CatalogEntry.id).where(
                    *_catalog_filter(category),
                    or_(
                        CatalogEntry.title.ilike(pattern),
                        CatalogEntry.description.ilike(pattern),
                        CatalogEntry.category.ilike(pattern),
                        CatalogEntry.url.ilike(pattern),
                    ),
                )
            ))
            match_reasons = {entry_id: "Text match" for entry_id in ranked_ids}
    elif sort == "recommended":
        recommended = await _recommended_ids(session, user.id, category)
        # New users still get a useful catalog instead of an empty state.
        ranked_ids = recommended or None

    subscriber_counts = (
        select(Feed.url.label("url"), func.count(Subscription.id).label("subscriber_count"))
        .join(Subscription, Subscription.feed_id == Feed.id)
        .group_by(Feed.url)
        .subquery()
    )
    stmt = (
        select(
            CatalogEntry,
            Subscription.feed_id,
            func.coalesce(subscriber_counts.c.subscriber_count, 0),
        )
        .outerjoin(Feed, Feed.url == CatalogEntry.url)
        .outerjoin(
            Subscription,
            and_(Subscription.feed_id == Feed.id, Subscription.user_id == user.id),
        )
        .outerjoin(subscriber_counts, subscriber_counts.c.url == CatalogEntry.url)
        .where(*_catalog_filter(category))
    )
    if ranked_ids is not None:
        if not ranked_ids:
            return []
        rank_order = case({entry_id: rank for rank, entry_id in enumerate(ranked_ids)}, value=CatalogEntry.id)
        stmt = stmt.where(CatalogEntry.id.in_(ranked_ids)).order_by(rank_order)
    elif sort == "popular":
        stmt = stmt.order_by(func.coalesce(subscriber_counts.c.subscriber_count, 0).desc(), func.lower(CatalogEntry.title))
    else:
        stmt = stmt.order_by(CatalogEntry.category, func.lower(CatalogEntry.title))

    rows = await session.execute(stmt)
    return [
        CatalogEntryOut(
            id=entry.id,
            url=entry.url,
            title=entry.title,
            description=entry.description,
            site_url=entry.site_url,
            category=entry.category,
            source_host=(urlsplit(entry.site_url or entry.final_url or entry.url).hostname or ""),
            content_type=entry.content_type,
            health_status=entry.health_status,
            item_count=entry.item_count,
            latest_item_at=entry.latest_item_at,
            preview_items=entry.preview_items or [],
            subscriber_count=subscriber_count,
            match_reason=match_reasons.get(entry.id),
            feed_id=feed_id,
            subscribed=feed_id is not None,
        )
        for entry, feed_id, subscriber_count in rows
    ]


@router.get("/categories", response_model=list[CatalogCategoryOut])
async def list_categories(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(CatalogEntry.category, func.count())
        .where(CatalogEntry.is_active.is_(True))
        .group_by(CatalogEntry.category)
        .order_by(CatalogEntry.category)
    )
    return [CatalogCategoryOut(name=name, count=count) for name, count in rows]


def _preview_summary(content_html: str) -> str | None:
    # nh3 re-escapes entities when stripping tags; this text renders as-is.
    text = html.unescape(strip_html(content_html))
    if not text:
        return None
    if len(text) <= PREVIEW_SUMMARY_CHARS:
        return text
    clipped = text[:PREVIEW_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{clipped}…"


@router.get("/{entry_id}/preview", response_model=CatalogPreviewOut)
async def preview_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Fetch a live snapshot of a catalog feed for the detail view."""
    entry = await session.scalar(
        select(CatalogEntry).where(CatalogEntry.id == entry_id, CatalogEntry.is_active.is_(True))
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    cached = _preview_cache.get(entry_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        parsed = await fetch_feed_data(entry.url)
    except (FeedParseError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="The feed could not be reached right now"
        ) from exc
    preview = CatalogPreviewOut(
        title=parsed.title or entry.title,
        description=parsed.description or entry.description,
        site_url=parsed.site_url or entry.site_url,
        fetched_at=datetime.now(timezone.utc),
        items=[
            CatalogPreviewItemOut(
                title=article.title,
                url=article.url,
                author=article.author,
                published_at=article.published_at,
                summary=_preview_summary(article.content_html),
            )
            for article in parsed.articles[:PREVIEW_ITEM_LIMIT]
        ],
    )
    _preview_cache[entry_id] = (time.monotonic() + PREVIEW_TTL_SECONDS, preview)
    return preview


@router.post("/submissions", response_model=CatalogSubmissionOut, status_code=201)
async def submit_feed(
    body: CatalogSubmissionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    url = body.url.strip()
    try:
        parsed = await fetch_feed_data(url, require_articles=True)
    except (FeedParseError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Could not fetch a valid feed at that URL") from exc
    if not (parsed.description or "").strip():
        raise HTTPException(status_code=422, detail="The feed has no description")
    submission = CatalogSubmission(
        user_id=user.id,
        url=url,
        category=body.category,
        note=body.note,
    )
    session.add(submission)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="This feed has already been submitted") from exc
    await session.refresh(submission)
    return CatalogSubmissionOut(
        id=submission.id,
        url=submission.url,
        category=submission.category,
        status=submission.status,
    )
