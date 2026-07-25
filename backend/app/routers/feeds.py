import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import queue
from ..deps import CurrentUser, DbSession
from ..extractor import SUMMARIZABLE_FEED_HTML_CHARS
from ..fetcher import FeedParseError, FeedRateLimited, discover_feed_url, refresh_feed
from ..models import Article, Feed, Share, Subscription, User, UserArticleState
from ..schemas import AddFeedIn, FeedOut, FeedSettingsIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", tags=["feeds"])


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


async def _get_subscribed_feed(session: AsyncSession, user: User, feed_id: int) -> Feed:
    # Hidden import feeds have no user-facing settings, refresh, or
    # unsubscribe — they 404 here like any other feed the user can't manage.
    feed = await session.scalar(
        select(Feed)
        .join(Subscription, and_(Subscription.feed_id == Feed.id, Subscription.user_id == user.id))
        .where(Feed.id == feed_id, Feed.owner_user_id.is_(None))
    )
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


def retention_visible():
    """Articles the subscriber can still see: no retention set, young enough,
    or saved (saved articles are exempt from retention). Requires Subscription
    and UserArticleState to be (outer-)joined in the enclosing statement."""
    return or_(
        Subscription.retention_days.is_(None),
        func.coalesce(Article.published_at, Article.fetched_at)
        >= func.now() - func.make_interval(0, 0, 0, Subscription.retention_days),
        UserArticleState.is_saved.is_(True),
    )


def _enrich_pending():
    """The worker's enrich stage still owes this article full text or an image."""
    return and_(
        Article.full_text_fetched_at.is_(None),
        or_(Article.full_text == "", Article.image_url.is_(None)),
    )


def _summary_pending():
    """The worker's summarize stage still owes this article a summary.

    Mirrors `enrich_and_summarize`'s summarize query exactly — including the
    thin-content exclusion — so articles the worker will never summarize don't
    keep the progress indicator spinning forever.
    """
    return and_(
        Feed.ai_enabled.is_(True),
        Article.summary_short == "",
        Article.summary_skipped_reason.is_(None),
        or_(
            Article.full_text != "",
            Article.full_text_fetched_at.is_(None),
            func.length(Article.content_html) > SUMMARIZABLE_FEED_HTML_CHARS,
        ),
    )


def _feed_list_stmt(user_id: int):
    visible = retention_visible()
    return (
        select(
            Feed,
            func.count(Article.id).filter(visible).label("article_count"),
            func.count(Article.id)
            .filter(
                visible,
                or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False)),
            )
            .label("unread_count"),
            # Mirrors both worker stages: full_text_fetched_at is stamped even
            # on failure and the summarize predicate excludes the articles the
            # worker skips for good, so this always converges to 0.
            func.count(Article.id)
            .filter(visible, or_(_enrich_pending(), _summary_pending()))
            .label("pending_count"),
            Subscription,
        )
        .join(Subscription, and_(Subscription.feed_id == Feed.id, Subscription.user_id == user_id))
        .outerjoin(Article, Article.feed_id == Feed.id)
        .outerjoin(
            UserArticleState,
            and_(UserArticleState.article_id == Article.id, UserArticleState.user_id == user_id),
        )
        # The user's hidden "Imported" feed is not a subscription they manage.
        .where(Feed.owner_user_id.is_(None))
        .group_by(Feed.id, Subscription.id)
        .order_by(func.coalesce(Subscription.title_override, Feed.title))
    )


def _to_feed_out(
    feed: Feed,
    article_count: int,
    unread_count: int,
    pending_count: int,
    subscription: Subscription,
) -> FeedOut:
    return FeedOut(
        id=feed.id,
        url=feed.url,
        title=subscription.title_override or feed.display_title,
        site_url=feed.site_url,
        description=feed.description,
        last_fetched_at=feed.last_fetched_at,
        article_count=article_count,
        unread_count=unread_count,
        pending_count=pending_count,
        view_override=subscription.view_override,
        title_override=subscription.title_override,
        sort_order=subscription.sort_order,
        retention_days=subscription.retention_days,
        is_muted=subscription.is_muted,
        ai_enabled=feed.ai_enabled,
        image_gen_enabled=feed.image_gen_enabled,
        refresh_interval_minutes=feed.refresh_interval_minutes,
    )


@router.get("", response_model=list[FeedOut])
async def list_feeds(user: CurrentUser, session: DbSession):
    rows = await session.execute(_feed_list_stmt(user.id))
    return [_to_feed_out(*row) for row in rows]


def _explain_fetch_failure(url: str, exc: Exception) -> str:
    """Say which of the three things went wrong, so the user knows what to fix."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "That site refused our request for its feed"
        if status == 404:
            return "Nothing is published at that URL (404)"
        return f"That site answered with an error ({status})"
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach {urlsplit(url).netloc or 'that address'}"
    if isinstance(exc, FeedParseError):
        return str(exc)
    return "Could not fetch or parse a feed at that URL"


async def _create_feed(session: AsyncSession, url: str) -> Feed:
    """Insert a feed and back-fill its first articles. Raises on a bad URL."""
    feed = Feed(url=url)
    session.add(feed)
    await session.flush()
    try:
        await refresh_feed(session, feed, require_articles=True)
    except FeedRateLimited as exc:
        # The feed exists — the publisher is just throttling server-side
        # fetches. Subscribe now (title falls back to the URL) and let the
        # poller backfill stories once the limit clears.
        logger.info("Subscribing to %s without an initial fetch: %s", url, exc)
    return feed


async def _backfill_existing(session: AsyncSession, feed: Feed, url: str) -> None:
    """A feed row someone already created but that holds no articles yet."""
    has_articles = await session.scalar(
        select(func.count()).select_from(Article).where(Article.feed_id == feed.id)
    )
    if has_articles:
        return
    try:
        await refresh_feed(session, feed, require_articles=True)
    except FeedRateLimited as exc:
        logger.info("Subscribing to %s without a revalidation fetch: %s", url, exc)
    except Exception as exc:
        await session.rollback()
        logger.warning("Existing empty feed is no longer valid %s: %s", url, exc)
        raise HTTPException(
            status_code=400, detail="This feed is empty or no longer available"
        ) from exc


async def _resolve_subscribe_target(session: AsyncSession, url: str) -> Feed:
    """The Feed row to subscribe to, creating it on first use.

    A URL that isn't itself a feed gets one autodiscovery pass before we give
    up: pasting the site you're reading is the common case, and the feed we
    find is what the row is keyed on, so two users arriving from the site and
    from its .xml share one feed.
    """
    feed = await session.scalar(select(Feed).where(Feed.url == url))
    if feed is not None:
        await _backfill_existing(session, feed, url)
        return feed

    try:
        return await _create_feed(session, url)
    except Exception as exc:
        await session.rollback()
        logger.warning("Failed to fetch feed %s: %s", url, exc)
        failure = exc

    discovered = await discover_feed_url(url, require_articles=True)
    if discovered is None or discovered == url:
        raise HTTPException(
            status_code=400, detail=_explain_fetch_failure(url, failure)
        ) from failure
    logger.info("Autodiscovered feed %s for %s", discovered, url)

    feed = await session.scalar(select(Feed).where(Feed.url == discovered))
    if feed is not None:
        await _backfill_existing(session, feed, discovered)
        return feed
    try:
        return await _create_feed(session, discovered)
    except Exception as exc:
        await session.rollback()
        logger.warning("Autodiscovered feed %s did not load: %s", discovered, exc)
        raise HTTPException(
            status_code=400, detail=_explain_fetch_failure(discovered, exc)
        ) from exc


@router.post("", response_model=FeedOut, status_code=201)
async def add_feed(
    body: AddFeedIn,
    user: CurrentUser,
    session: DbSession,
):
    # Read the id before resolving: a failed first fetch rolls the session
    # back, which expires `user` and makes any later attribute access a lazy
    # load the async session can't service.
    user_id = user.id
    url = _normalize_url(body.url)
    feed = await _resolve_subscribe_target(session, url)

    # Quick settings chosen at subscribe time. The global switches share PATCH
    # /feeds/{id}/settings semantics (any subscriber may flip them); is_muted
    # is scoped to this user's subscription.
    if body.ai_enabled is not None:
        feed.ai_enabled = body.ai_enabled
    if body.image_gen_enabled is not None:
        feed.image_gen_enabled = body.image_gen_enabled

    already = await session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.feed_id == feed.id)
    )
    if already is None:
        already = Subscription(user_id=user_id, feed_id=feed.id)
        session.add(already)
    if body.is_muted is not None:
        already.is_muted = body.is_muted
    await session.commit()

    # Background: fetch og:images + full text, then pre-generate summaries.
    await queue.enqueue("enrich_feed", feed.id)

    row = (await session.execute(_feed_list_stmt(user_id).where(Feed.id == feed.id))).one()
    return _to_feed_out(*row)


@router.post("/{feed_id}/refresh", response_model=FeedOut)
async def refresh(
    feed_id: int,
    user: CurrentUser,
    session: DbSession,
):
    feed = await _get_subscribed_feed(session, user, feed_id)
    try:
        await refresh_feed(session, feed)
    except Exception as exc:
        # Log before rollback: rollback expires `feed`, so touching feed.url
        # afterwards would trigger a lazy load the async session can't service.
        logger.warning("Failed to refresh feed %s: %s", feed.url, exc)
        await session.rollback()
        raise HTTPException(
            status_code=502, detail="Could not refresh this feed right now"
        ) from exc
    await queue.enqueue("enrich_feed", feed.id)
    row = (await session.execute(_feed_list_stmt(user.id).where(Feed.id == feed.id))).one()
    return _to_feed_out(*row)


@router.patch("/{feed_id}/settings", response_model=FeedOut)
async def update_feed_settings(
    feed_id: int,
    body: FeedSettingsIn,
    user: CurrentUser,
    session: DbSession,
):
    feed = await _get_subscribed_feed(session, user, feed_id)
    subscription = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.feed_id == feed.id)
    )
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Nothing to update")

    # Per-subscription overrides: explicit null clears back to the default.
    if "view_override" in updates:
        subscription.view_override = updates["view_override"]
    if "title_override" in updates:
        subscription.title_override = (updates["title_override"] or "").strip() or None
    if "sort_order" in updates:
        # "newest" is the default; store it as NULL so it never diverges.
        sort = updates["sort_order"]
        subscription.sort_order = None if sort == "newest" else sort
    if "retention_days" in updates:
        subscription.retention_days = updates["retention_days"]
    if updates.get("is_muted") is not None:
        subscription.is_muted = updates["is_muted"]

    # Global feed settings, shared by every subscriber.
    if updates.get("ai_enabled") is not None:
        feed.ai_enabled = updates["ai_enabled"]
    if updates.get("image_gen_enabled") is not None:
        feed.image_gen_enabled = updates["image_gen_enabled"]
    if updates.get("refresh_interval_minutes") is not None:
        feed.refresh_interval_minutes = updates["refresh_interval_minutes"]

    await session.commit()
    row = (await session.execute(_feed_list_stmt(user.id).where(Feed.id == feed.id))).one()
    return _to_feed_out(*row)


@router.delete("/{feed_id}", status_code=204)
async def unsubscribe(
    feed_id: int,
    user: CurrentUser,
    session: DbSession,
):
    feed = await _get_subscribed_feed(session, user, feed_id)
    subscription = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.feed_id == feed.id)
    )
    await session.delete(subscription)
    await session.flush()

    # Garbage-collect the feed if nobody subscribes and no share references its articles.
    has_subscribers = await session.scalar(select(exists().where(Subscription.feed_id == feed.id)))
    has_shares = await session.scalar(
        select(exists().where(and_(Share.article_id == Article.id, Article.feed_id == feed.id)))
    )
    if not has_subscribers and not has_shares:
        await session.delete(feed)
    await session.commit()
