import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlsplit

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, case, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import embeddings, ranking
from ..config import settings
from ..deps import CurrentUser, DbSession
from ..fetcher import FeedParseError, FeedRateLimited, fetch_feed_data, strip_html
from ..models import (
    CatalogEntry,
    CatalogEntryEmbedding,
    CatalogSubmission,
    Feed,
    Subscription,
)
from ..schemas import (
    CatalogCategoryOut,
    CatalogEntryOut,
    CatalogPreviewItemOut,
    CatalogPreviewOut,
    CatalogSubmissionIn,
    CatalogSubmissionOut,
    SmartFeedOut,
    SmartFeedResolveOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/catalog", tags=["catalog"])
PREVIEW_ITEM_LIMIT = 8
PREVIEW_SUMMARY_CHARS = 240
PREVIEW_TTL_SECONDS = 600
# Repeated modal opens shouldn't re-hit publishers; the catalog is small
# (hundreds of entries), so an unbounded per-process cache is fine. Keyed by
# feed URL so catalog entries and smart-feed topics share one cache.
_preview_cache: dict[str, tuple[float, CatalogPreviewOut]] = {}
# When a publisher 429s us, back off instead of burning its rate-limit bucket
# on every modal open: remember the refusal and answer 503 without re-fetching.
RATE_LIMIT_TTL_SECONDS = 60
_preview_rate_limited: dict[str, tuple[float, str]] = {}


@dataclass(frozen=True)
class SmartFeedProvider:
    """A feed source parameterized by a user-supplied topic. `slug` providers
    take an identifier (subreddit, tag) and also accept the topic's page URL
    pasted verbatim; `query` providers accept free text."""

    key: str
    name: str
    description: str
    site_url: str
    category: str
    topic_label: str
    topic_hint: str
    url_template: str  # receives the url-encoded topic as {topic}
    title_template: str  # receives the plain topic as {topic}
    example_topics: tuple[str, ...] = ()
    kind: Literal["slug", "query"] = "slug"
    # slug providers: extract the topic from a pasted URL, strip UI prefixes
    # ("r/", "#"), and validate the final slug.
    url_topic_re: re.Pattern | None = None
    strip_prefixes: tuple[str, ...] = ()
    topic_re: re.Pattern = field(default=re.compile(r"^[A-Za-z0-9_]{1,80}$"))
    lowercase: bool = False


SMART_FEEDS: dict[str, SmartFeedProvider] = {
    provider.key: provider
    for provider in (
        SmartFeedProvider(
            key="reddit",
            name="Reddit",
            description="Follow any subreddit as a feed of its newest posts.",
            site_url="https://www.reddit.com",
            category="Communities",
            topic_label="Subreddit",
            topic_hint="programming, or paste reddit.com/r/programming",
            url_template="https://www.reddit.com/r/{topic}/.rss",
            title_template="r/{topic}",
            example_topics=("programming", "science", "worldnews"),
            url_topic_re=re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)", re.IGNORECASE),
            strip_prefixes=("r/",),
            topic_re=re.compile(r"^[A-Za-z0-9_]{2,50}$"),
        ),
        SmartFeedProvider(
            key="google-news",
            name="Google News",
            description="A news search on any topic, updated as stories break.",
            site_url="https://news.google.com",
            category="News",
            topic_label="Topic",
            topic_hint="climate change, Tel Aviv, SpaceX…",
            url_template="https://news.google.com/rss/search?q={topic}&hl=en-US&gl=US&ceid=US:en",
            title_template="Google News · {topic}",
            example_topics=("artificial intelligence", "renewable energy"),
            kind="query",
        ),
        SmartFeedProvider(
            key="hacker-news",
            name="Hacker News",
            description="New Hacker News stories matching a search phrase.",
            site_url="https://news.ycombinator.com",
            category="Tech",
            topic_label="Search phrase",
            topic_hint="rust, self-hosting, databases…",
            url_template="https://hnrss.org/newest?q={topic}",
            title_template="Hacker News · {topic}",
            example_topics=("rust", "postgres"),
            kind="query",
        ),
        SmartFeedProvider(
            key="medium",
            name="Medium",
            description="Stories published under any Medium tag.",
            site_url="https://medium.com",
            category="Writing",
            topic_label="Tag",
            topic_hint="machine-learning, or paste medium.com/tag/machine-learning",
            url_template="https://medium.com/feed/tag/{topic}",
            title_template="Medium · {topic}",
            example_topics=("machine-learning", "startup"),
            url_topic_re=re.compile(r"medium\.com/(?:feed/)?tag/([A-Za-z0-9-]+)", re.IGNORECASE),
            topic_re=re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$"),
            lowercase=True,
        ),
        SmartFeedProvider(
            key="mastodon",
            name="Mastodon",
            description="Public posts for any hashtag on mastodon.social.",
            site_url="https://mastodon.social",
            category="Communities",
            topic_label="Hashtag",
            topic_hint="photography, or paste mastodon.social/tags/photography",
            url_template="https://mastodon.social/tags/{topic}.rss",
            title_template="#{topic}",
            example_topics=("photography", "opensource"),
            url_topic_re=re.compile(r"/tags/([A-Za-z0-9_]+)", re.IGNORECASE),
            strip_prefixes=("#",),
            topic_re=re.compile(r"^[A-Za-z0-9_]{1,64}$"),
        ),
    )
}


def _looks_like_url(raw: str) -> bool:
    return (
        "://" in raw or raw.lower().startswith("www.") or "/" in raw and "." in raw.split("/", 1)[0]
    )


def resolve_smart_topic(provider: SmartFeedProvider, raw: str) -> SmartFeedResolveOut:
    """Normalize a typed topic — or a pasted page URL — into a feed URL."""
    topic = raw.strip()
    if not topic:
        raise ValueError(f"Enter a {provider.topic_label.lower()}")
    if provider.kind == "query":
        # A pasted Google-News-style search URL still resolves to its query.
        if _looks_like_url(topic):
            query = parse_qs(urlsplit(topic if "://" in topic else f"https://{topic}").query).get(
                "q"
            )
            if query and query[0].strip():
                topic = query[0].strip()
        if len(topic) > 120:
            raise ValueError(f"{provider.topic_label} is too long")
        return SmartFeedResolveOut(
            key=provider.key,
            topic=topic,
            url=provider.url_template.format(topic=quote_plus(topic)),
            title=provider.title_template.format(topic=topic),
        )
    if _looks_like_url(topic):
        match = provider.url_topic_re.search(topic) if provider.url_topic_re else None
        if match is None:
            raise ValueError(
                f"That link does not look like a {provider.name} {provider.topic_label.lower()} URL"
            )
        topic = match.group(1)
    for prefix in provider.strip_prefixes:
        if topic.lower().startswith(prefix.lower()):
            topic = topic[len(prefix) :]
    topic = topic.strip().strip("/")
    if provider.lowercase:
        topic = re.sub(r"\s+", "-", topic.lower())
    if not provider.topic_re.fullmatch(topic):
        raise ValueError(
            f"That does not look like a valid {provider.name} {provider.topic_label.lower()}"
        )
    return SmartFeedResolveOut(
        key=provider.key,
        topic=topic,
        url=provider.url_template.format(topic=quote(topic, safe="")),
        title=provider.title_template.format(topic=topic),
    )


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
        .limit(ranking.SEARCH_POOL)
    )
    keyword_ids = list(await session.scalars(keyword_stmt))
    pattern = f"%{q}%"
    partial_ids = list(
        await session.scalars(
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
            .limit(ranking.SEARCH_POOL)
        )
    )
    text_ids = list(dict.fromkeys([*keyword_ids, *partial_ids]))
    if len(q) < 3 or not embeddings.is_configured():
        return (text_ids, {entry_id: "Keyword match" for entry_id in text_ids})
    try:
        query_vector = await embeddings.embed_query(q)
    except Exception as exc:
        logger.warning("Catalog query embedding failed, using full-text search: %s", exc)
        return (text_ids, {entry_id: "Keyword match" for entry_id in text_ids})
    vector_ids = list(
        await session.scalars(
            select(CatalogEntry.id)
            .join(CatalogEntryEmbedding)
            .where(
                *_catalog_filter(category),
                CatalogEntryEmbedding.model == settings.openai_embedding_model,
            )
            .order_by(CatalogEntryEmbedding.embedding.cosine_distance(query_vector))
            .limit(ranking.SEARCH_POOL)
        )
    )
    reasons: dict[int, set[str]] = {}
    for label, leg in (("Semantic match", vector_ids), ("Keyword match", text_ids)):
        for entry_id in leg:
            reasons.setdefault(entry_id, set()).add(label)
    ranked = ranking.rrf_fuse(vector_ids, text_ids)
    labels = {
        entry_id: "Keyword and semantic match"
        if len(reasons[entry_id]) == 2
        else next(iter(reasons[entry_id]))
        for entry_id in ranked
    }
    return ranked, labels


async def _recommended_ids(session: AsyncSession, user_id: int, category: str | None) -> list[int]:
    """Rank catalog entries near the centroid of the user's subscriptions."""
    if not embeddings.is_configured():
        return []
    subscribed = list(
        await session.scalars(
            select(CatalogEntryEmbedding.embedding)
            .join(CatalogEntry, CatalogEntry.id == CatalogEntryEmbedding.catalog_entry_id)
            .join(Feed, Feed.url == CatalogEntry.url)
            .join(Subscription, Subscription.feed_id == Feed.id)
            .where(
                Subscription.user_id == user_id,
                CatalogEntryEmbedding.model == settings.openai_embedding_model,
            )
        )
    )
    if not subscribed:
        return []
    centroid = ranking.centroid([[float(x) for x in vector] for vector in subscribed])
    return list(
        await session.scalars(
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
        )
    )


@router.get("", response_model=list[CatalogEntryOut])
async def browse_catalog(
    user: CurrentUser,
    session: DbSession,
    q: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=64),
    sort: Literal["name", "popular", "recommended"] = "name",
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
            ranked_ids = list(
                await session.scalars(
                    select(CatalogEntry.id).where(
                        *_catalog_filter(category),
                        or_(
                            CatalogEntry.title.ilike(pattern),
                            CatalogEntry.description.ilike(pattern),
                            CatalogEntry.category.ilike(pattern),
                            CatalogEntry.url.ilike(pattern),
                        ),
                    )
                )
            )
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
        rank_order = case(
            {entry_id: rank for rank, entry_id in enumerate(ranked_ids)}, value=CatalogEntry.id
        )
        stmt = stmt.where(CatalogEntry.id.in_(ranked_ids)).order_by(rank_order)
    elif sort == "popular":
        stmt = stmt.order_by(
            func.coalesce(subscriber_counts.c.subscriber_count, 0).desc(),
            func.lower(CatalogEntry.title),
        )
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
    user: CurrentUser,
    session: DbSession,
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


async def _cached_preview(
    feed_url: str,
    *,
    fallback_title: str,
    fallback_description: str | None = None,
    fallback_site_url: str | None = None,
) -> CatalogPreviewOut:
    """Fetch a live snapshot of a feed, memoized by URL for PREVIEW_TTL_SECONDS."""
    cached = _preview_cache.get(feed_url)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    limited = _preview_rate_limited.get(feed_url)
    if limited and limited[0] > time.monotonic():
        raise HTTPException(status_code=503, detail=limited[1])
    try:
        parsed = await fetch_feed_data(feed_url)
    except FeedRateLimited as exc:
        detail = (
            f"{exc.host} is rate-limiting our preview requests right now. "
            "Try again in a minute or two."
        )
        _preview_rate_limited[feed_url] = (time.monotonic() + RATE_LIMIT_TTL_SECONDS, detail)
        raise HTTPException(status_code=503, detail=detail) from exc
    except (FeedParseError, ValueError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="The feed could not be reached right now"
        ) from exc
    # Item links may be relative (resolve against the feed URL) or missing
    # entirely (guid-only items) — never emit an empty-string href.
    base_url = parsed.final_url or feed_url
    preview = CatalogPreviewOut(
        title=parsed.title or fallback_title,
        description=parsed.description or fallback_description,
        site_url=parsed.site_url or fallback_site_url,
        fetched_at=datetime.now(UTC),
        items=[
            CatalogPreviewItemOut(
                title=article.title,
                url=urljoin(base_url, article.url) if article.url else None,
                author=article.author,
                published_at=article.published_at,
                summary=_preview_summary(article.content_html),
            )
            for article in parsed.articles[:PREVIEW_ITEM_LIMIT]
        ],
    )
    _preview_cache[feed_url] = (time.monotonic() + PREVIEW_TTL_SECONDS, preview)
    return preview


@router.get("/smart", response_model=list[SmartFeedOut])
async def list_smart_feeds(user: CurrentUser):
    return [
        SmartFeedOut(
            key=provider.key,
            name=provider.name,
            description=provider.description,
            site_url=provider.site_url,
            category=provider.category,
            topic_label=provider.topic_label,
            topic_hint=provider.topic_hint,
            example_topics=list(provider.example_topics),
        )
        for provider in SMART_FEEDS.values()
    ]


def _smart_provider(key: str) -> SmartFeedProvider:
    provider = SMART_FEEDS.get(key)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown smart feed")
    return provider


@router.get("/smart/{key}/resolve", response_model=SmartFeedResolveOut)
async def resolve_smart_feed(
    key: str,
    user: CurrentUser,
    topic: str = Query(min_length=1, max_length=2048),
):
    """Turn a topic (or a pasted topic-page URL) into a concrete feed URL."""
    try:
        return resolve_smart_topic(_smart_provider(key), topic)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/smart/{key}/preview", response_model=CatalogPreviewOut)
async def preview_smart_feed(
    key: str,
    user: CurrentUser,
    topic: str = Query(min_length=1, max_length=2048),
):
    """Server-side preview fallback for browsers blocked by feed CORS policies."""
    try:
        resolved = resolve_smart_topic(_smart_provider(key), topic)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _cached_preview(resolved.url, fallback_title=resolved.title)


@router.get("/{entry_id}/preview", response_model=CatalogPreviewOut)
async def preview_entry(
    entry_id: int,
    user: CurrentUser,
    session: DbSession,
):
    """Fetch a live snapshot of a catalog feed for the detail view."""
    entry = await session.scalar(
        select(CatalogEntry).where(CatalogEntry.id == entry_id, CatalogEntry.is_active.is_(True))
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return await _cached_preview(
        entry.url,
        fallback_title=entry.title,
        fallback_description=entry.description,
        fallback_site_url=entry.site_url,
    )


@router.post("/submissions", response_model=CatalogSubmissionOut, status_code=201)
async def submit_feed(
    body: CatalogSubmissionIn,
    user: CurrentUser,
    session: DbSession,
):
    url = body.url.strip()
    try:
        parsed = await fetch_feed_data(url, require_articles=True)
    except FeedRateLimited:
        raise  # the app-level 503 handler owns the message
    except (FeedParseError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail="Could not fetch a valid feed at that URL"
        ) from exc
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
