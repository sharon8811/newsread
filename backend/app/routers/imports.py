"""Ad-hoc URL imports.

Each user gets one hidden system feed ("Imported", non-null Feed.owner_user_id)
that they alone subscribe to. A pasted URL becomes an ordinary Article in that
feed, so the article page, projects, shares, and the worker's NER/embedding
pipeline all work unchanged; the feed itself never appears in feed listings and
is never polled. Imported articles stay out of the aggregate inbox (importing
means "read this now", not "queue it in my stream") — they live on the
Imported page, via GET /articles?feed_id=<import feed>.
"""

import hashlib
import logging
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crypto, db, fetcher, llm
from ..deps import CurrentUser, DbSession
from ..extractor import fetch_page
from ..fetcher import FeedParseError, derive_excerpt
from ..models import Article, Feed, Subscription
from ..schemas import ArticleDetail, ImportFeedOut, ImportIn
from ..summarizer import SummarySkipped, ThinContentError, generate_summaries
from .articles import to_list_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])

IMPORT_FEED_TITLE = "Imported"

# Query params that identify the click, not the page — stripped so the same
# article pasted from different places dedups to one import.
_TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref_src"}

# Everything summaries need from the source row when the same URL was already
# ingested via a feed: the import reuses that work instead of re-fetching.
_COPY_FIELDS = (
    "url",
    "comments_url",
    "title",
    "author",
    "content_html",
    "excerpt",
    "image_url",
    "full_text",
    "full_text_fetched_at",
    "summary_short",
    "summary_medium",
    "summary",
    "summary_model",
    "summary_generated_at",
    "summary_skipped_reason",
)


def normalize_import_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in _TRACKING_PARAMS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), "")
    )


def _guid_for(url: str) -> str:
    # URLs run up to 2048 chars but guid is String(1024) — hash, don't truncate.
    return hashlib.sha256(url.encode()).hexdigest()


async def _import_feed(session: AsyncSession, user_id: int) -> Feed:
    """The user's hidden import feed (plus its subscription), created lazily."""
    feed = await session.scalar(select(Feed).where(Feed.owner_user_id == user_id))
    if feed is not None:
        return feed
    feed = Feed(
        url=f"newsread://imported/{user_id}",
        title=IMPORT_FEED_TITLE,
        owner_user_id=user_id,
        # Imports usually carry an og:image; never spend image-gen budget here.
        image_gen_enabled=False,
    )
    session.add(feed)
    try:
        await session.flush()
    except IntegrityError:
        # Two concurrent first imports raced; the unique owner constraint
        # elected a winner — use their row.
        await session.rollback()
        return await session.scalar(select(Feed).where(Feed.owner_user_id == user_id))
    session.add(Subscription(user_id=user_id, feed_id=feed.id))
    await session.flush()
    return feed


def _detail(article: Article, feed: Feed) -> ArticleDetail:
    item = to_list_item(article, feed.display_title, None, image_pending=False)
    return ArticleDetail(
        **item.model_dump(exclude={"entities", "image_pending"}),
        content_html=article.content_html,
        summary_model=article.summary_model,
        summary_skipped_reason=article.summary_skipped_reason,
        image_pending=False,
    )


async def process_import(article_id: int, user_id: int, config: llm.LLMConfig | None) -> None:
    """Background stage of an import: fetch the page (unless the row was
    copied with text already), then summarize with the importer's LLM. NER
    and embeddings converge via the normal worker cycles. Failures leave a
    row the article page can still render ("open original")."""
    async with db.SessionLocal() as session:
        article = await session.get(Article, article_id)
        if article is None:
            return
        if article.full_text_fetched_at is None:
            text, image, title = await fetch_page(article.url)
            if title:
                article.title = title
            if text:
                article.full_text = text
                if not article.excerpt:
                    article.excerpt = derive_excerpt(text)
            if image and not article.image_url:
                article.image_url = image[:2048]
            article.full_text_fetched_at = datetime.now(UTC)
            await session.commit()
        if article.summary and article.summary_short:
            return
        if article.summary_skipped_reason is not None or config is None:
            return
        try:
            async with llm.usage_tracker(
                session,
                user_id=user_id,
                feature="summary",
                config=config,
                log_label=f"Import summarization for article {article_id}",
                passthrough=(ThinContentError, SummarySkipped),
            ) as usage:
                await generate_summaries(
                    session,
                    article,
                    allow_refetch=False,
                    config=config,
                    usage=usage,
                    allow_vision=True,
                )
        except (ThinContentError, SummarySkipped):
            pass  # expected terminal states — the page falls back to the source
        except Exception as exc:
            # Background task: nothing upstream catches this, so log and leave
            # the article summariless (the on-demand summarize button remains).
            logger.warning("Import summarization for article %s failed: %s", article_id, exc)


@router.post("", response_model=ArticleDetail, status_code=201)
async def import_url(
    body: ImportIn,
    background: BackgroundTasks,
    response: Response,
    user: CurrentUser,
    session: DbSession,
):
    url = normalize_import_url(body.url)
    try:
        # Same SSRF guard as feed subscriptions: this endpoint fetches
        # arbitrary user-supplied URLs server-side.
        await fetcher._validate_public_url(url)
    except FeedParseError as exc:
        raise HTTPException(status_code=400, detail="Enter a public http(s) page URL") from exc

    feed = await _import_feed(session, user.id)
    guid = _guid_for(url)
    existing = await session.scalar(
        select(Article).where(Article.feed_id == feed.id, Article.guid == guid)
    )
    if existing is not None:
        await session.commit()  # a freshly created feed still needs persisting
        response.status_code = 200
        return _detail(existing, feed)

    # Copy-dedup: the same URL already ingested anywhere (feed or another
    # user's import) donates its extracted text and summaries — no re-fetch,
    # no second LLM call. Prefer a row whose summary already landed.
    source = await session.scalar(
        select(Article)
        .where(Article.url.in_({url, body.url.strip()}), Article.feed_id != feed.id)
        .order_by(Article.summary_generated_at.desc().nulls_last(), Article.id.desc())
        .limit(1)
    )
    article = Article(
        feed_id=feed.id,
        guid=guid,
        url=url,
        title=urlsplit(url).hostname or url,
        # Import time, not the source's publish date: the Imported list is a
        # history of when the user brought things in.
        published_at=datetime.now(UTC),
    )
    if source is not None:
        for field in _COPY_FIELDS:
            setattr(article, field, getattr(source, field))
        article.url = url
    session.add(article)
    await session.commit()
    await session.refresh(article)

    # A copied row may still lack summaries (source not summarized yet);
    # process_import skips whatever stage is already done.
    try:
        config = await llm.resolve_config(session, user.id)
    except crypto.TokenCryptoError:
        config = None  # a broken stored key must not fail the import itself
    background.add_task(process_import, article.id, user.id, config)
    return _detail(article, feed)


@router.get("/feed", response_model=ImportFeedOut)
async def import_feed(user: CurrentUser, session: DbSession):
    """The user's import feed id (created on first call) — the Imported page
    lists it via GET /articles?feed_id=..."""
    feed = await _import_feed(session, user.id)
    await session.commit()
    return ImportFeedOut(feed_id=feed.id)
