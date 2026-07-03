import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..fetcher import refresh_feed
from ..queue import enqueue
from ..models import Article, Feed, Share, Subscription, User, UserArticleState
from ..schemas import AddFeedIn, FeedOut
from ..security import get_current_user

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


def _feed_list_stmt(user_id: int):
    return (
        select(
            Feed,
            func.count(Article.id).label("article_count"),
            func.count(Article.id)
            .filter(or_(UserArticleState.id.is_(None), UserArticleState.is_read.is_(False)))
            .label("unread_count"),
        )
        .join(Subscription, and_(Subscription.feed_id == Feed.id, Subscription.user_id == user_id))
        .outerjoin(Article, Article.feed_id == Feed.id)
        .outerjoin(
            UserArticleState,
            and_(UserArticleState.article_id == Article.id, UserArticleState.user_id == user_id),
        )
        .group_by(Feed.id)
        .order_by(Feed.title)
    )


def _to_feed_out(feed: Feed, article_count: int, unread_count: int) -> FeedOut:
    return FeedOut(
        id=feed.id,
        url=feed.url,
        title=feed.title or feed.url,
        site_url=feed.site_url,
        description=feed.description,
        last_fetched_at=feed.last_fetched_at,
        article_count=article_count,
        unread_count=unread_count,
    )


@router.get("", response_model=list[FeedOut])
async def list_feeds(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(_feed_list_stmt(user.id))
    return [_to_feed_out(feed, article_count, unread_count) for feed, article_count, unread_count in rows]


@router.post("", response_model=FeedOut, status_code=201)
async def add_feed(
    body: AddFeedIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    url = _normalize_url(body.url)
    feed = await session.scalar(select(Feed).where(Feed.url == url))

    if feed is None:
        feed = Feed(url=url)
        session.add(feed)
        await session.flush()
        try:
            await refresh_feed(session, feed)
        except Exception as exc:
            await session.rollback()
            logger.warning("Failed to fetch feed %s: %s", url, exc)
            raise HTTPException(
                status_code=400, detail="Could not fetch or parse a feed at that URL"
            )

    already = await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id, Subscription.feed_id == feed.id
        )
    )
    if already is None:
        session.add(Subscription(user_id=user.id, feed_id=feed.id))
        await session.commit()

    # Background: fetch og:images + full text, then pre-generate summaries.
    await enqueue("enrich_feed", feed.id)

    row = (
        await session.execute(_feed_list_stmt(user.id).where(Feed.id == feed.id))
    ).one()
    return _to_feed_out(row[0], row[1], row[2])


@router.post("/{feed_id}/refresh", response_model=FeedOut)
async def refresh(
    feed_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    feed = await _get_subscribed_feed(session, user, feed_id)
    try:
        await refresh_feed(session, feed)
    except Exception as exc:
        await session.rollback()
        logger.warning("Failed to refresh feed %s: %s", feed.url, exc)
        raise HTTPException(status_code=502, detail="Could not refresh this feed right now")
    await enqueue("enrich_feed", feed.id)
    row = (
        await session.execute(_feed_list_stmt(user.id).where(Feed.id == feed.id))
    ).one()
    return _to_feed_out(row[0], row[1], row[2])


@router.delete("/{feed_id}", status_code=204)
async def unsubscribe(
    feed_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    feed = await _get_subscribed_feed(session, user, feed_id)
    subscription = await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id, Subscription.feed_id == feed.id
        )
    )
    await session.delete(subscription)
    await session.flush()

    # Garbage-collect the feed if nobody subscribes and no share references its articles.
    has_subscribers = await session.scalar(
        select(exists().where(Subscription.feed_id == feed.id))
    )
    has_shares = await session.scalar(
        select(exists().where(and_(Share.article_id == Article.id, Article.feed_id == feed.id)))
    )
    if not has_subscribers and not has_shares:
        await session.delete(feed)
    await session.commit()
