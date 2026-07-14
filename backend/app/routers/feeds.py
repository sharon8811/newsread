import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, DbSession
from ..fetcher import FeedRateLimited, refresh_feed
from ..models import Article, Feed, Share, Subscription, User, UserArticleState
from ..queue import enqueue
from ..schemas import AddFeedIn, FeedOut, FeedSettingsIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", tags=["feeds"])


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


async def _get_subscribed_feed(session: AsyncSession, user: User, feed_id: int) -> Feed:
    feed = await session.scalar(
        select(Feed)
        .join(Subscription, and_(Subscription.feed_id == Feed.id, Subscription.user_id == user.id))
        .where(Feed.id == feed_id)
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
            # Mirrors the worker's enrich query: full_text_fetched_at is stamped
            # even on failure, so this always converges to 0.
            func.count(Article.id)
            .filter(
                visible,
                and_(
                    Article.full_text_fetched_at.is_(None),
                    or_(Article.full_text == "", Article.image_url.is_(None)),
                ),
            )
            .label("pending_count"),
            Subscription,
        )
        .join(Subscription, and_(Subscription.feed_id == Feed.id, Subscription.user_id == user_id))
        .outerjoin(Article, Article.feed_id == Feed.id)
        .outerjoin(
            UserArticleState,
            and_(UserArticleState.article_id == Article.id, UserArticleState.user_id == user_id),
        )
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
        title=subscription.title_override or feed.title or feed.url,
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


@router.post("", response_model=FeedOut, status_code=201)
async def add_feed(
    body: AddFeedIn,
    user: CurrentUser,
    session: DbSession,
):
    url = _normalize_url(body.url)
    feed = await session.scalar(select(Feed).where(Feed.url == url))

    if feed is None:
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
        except Exception as exc:
            await session.rollback()
            logger.warning("Failed to fetch feed %s: %s", url, exc)
            raise HTTPException(
                status_code=400, detail="Could not fetch or parse a feed at that URL"
            ) from exc
    else:
        has_articles = await session.scalar(
            select(func.count()).select_from(Article).where(Article.feed_id == feed.id)
        )
        if not has_articles:
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

    # Quick settings chosen at subscribe time. The global switches share PATCH
    # /feeds/{id}/settings semantics (any subscriber may flip them); is_muted
    # is scoped to this user's subscription.
    if body.ai_enabled is not None:
        feed.ai_enabled = body.ai_enabled
    if body.image_gen_enabled is not None:
        feed.image_gen_enabled = body.image_gen_enabled

    already = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.feed_id == feed.id)
    )
    if already is None:
        already = Subscription(user_id=user.id, feed_id=feed.id)
        session.add(already)
    if body.is_muted is not None:
        already.is_muted = body.is_muted
    await session.commit()

    # Background: fetch og:images + full text, then pre-generate summaries.
    await enqueue("enrich_feed", feed.id)

    row = (await session.execute(_feed_list_stmt(user.id).where(Feed.id == feed.id))).one()
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
    await enqueue("enrich_feed", feed.id)
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
