from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..access import accessible_article
from ..deps import CurrentUser, DbSession
from ..models import Article, Feed, ReadingActivity
from ..schemas import (
    ActivityArticleOut,
    ActivityDayOut,
    ActivityFeedOut,
    ActivityRange,
    ActivitySummaryOut,
    HeartbeatIn,
)
from ..timewindow import window_bounds

router = APIRouter(prefix="/activity", tags=["activity"])

# How long the streak lookback goes; a streak longer than a year reads as "365+".
STREAK_LOOKBACK_DAYS = 366

TOP_LIMIT = 5


@router.post("/heartbeat", status_code=204)
async def heartbeat(
    body: HeartbeatIn,
    user: CurrentUser,
    session: DbSession,
):
    """Add `seconds` of reading time to (user, article, day, source). Clients
    send one every ~30s of active reading plus a final flush on leave."""
    article = await accessible_article(session, user.id, body.article_id)

    # A client-local date is at most ~1 day away from the server's; anything
    # further is a broken clock and would spray junk across the chart.
    if abs((body.day - date.today()).days) > 2:
        raise HTTPException(status_code=422, detail="Day too far from current date")

    stmt = (
        pg_insert(ReadingActivity)
        .values(
            user_id=user.id,
            article_id=article.id,
            day=body.day,
            source=body.source,
            seconds=body.seconds,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "article_id", "day", "source"],
            set_={"seconds": ReadingActivity.seconds + body.seconds},
        )
    )
    await session.execute(stmt)
    await session.commit()


@router.get("/summary", response_model=ActivitySummaryOut)
async def summary(
    user: CurrentUser,
    session: DbSession,
    range_: ActivityRange = Query("week", alias="range"),
    today: date | None = Query(None, description="Client-local date"),
):
    if today is None:
        today = date.today()
    window, start, prev_start = window_bounds(today, range_)

    in_window = (
        ReadingActivity.user_id == user.id,
        ReadingActivity.day >= start,
        ReadingActivity.day <= today,
    )

    rows = await session.execute(
        select(ReadingActivity.day, func.sum(ReadingActivity.seconds))
        .where(*in_window)
        .group_by(ReadingActivity.day)
    )
    by_day = {day: secs for day, secs in rows.all()}
    days = [
        ActivityDayOut(day=d, seconds=by_day.get(d, 0))
        for d in (start + timedelta(days=i) for i in range(window))
    ]
    total_seconds = sum(d.seconds for d in days)

    prev_total_seconds = (
        await session.scalar(
            select(func.coalesce(func.sum(ReadingActivity.seconds), 0)).where(
                ReadingActivity.user_id == user.id,
                ReadingActivity.day >= prev_start,
                ReadingActivity.day < start,
            )
        )
    ) or 0

    # Streak: consecutive active days ending today — or yesterday, so the
    # streak doesn't read as broken before today's first article.
    active_days = set(
        (
            await session.scalars(
                select(ReadingActivity.day)
                .where(
                    ReadingActivity.user_id == user.id,
                    ReadingActivity.day <= today,
                    ReadingActivity.day > today - timedelta(days=STREAK_LOOKBACK_DAYS),
                )
                .group_by(ReadingActivity.day)
            )
        ).all()
    )
    streak_days = 0
    cursor = today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        streak_days += 1
        cursor -= timedelta(days=1)

    secs = func.sum(ReadingActivity.seconds).label("secs")
    feed_rows = await session.execute(
        select(Feed.id, Feed.title, Feed.url, secs)
        .select_from(ReadingActivity)
        .join(Article, Article.id == ReadingActivity.article_id)
        .join(Feed, Feed.id == Article.feed_id)
        .where(*in_window)
        .group_by(Feed.id)
        .order_by(desc("secs"))
        .limit(TOP_LIMIT)
    )
    top_feeds = [
        ActivityFeedOut(feed_id=fid, title=title or url, seconds=s)
        for fid, title, url, s in feed_rows.all()
    ]

    article_rows = await session.execute(
        select(Article.id, Article.title, Feed.title, Feed.url, secs)
        .select_from(ReadingActivity)
        .join(Article, Article.id == ReadingActivity.article_id)
        .join(Feed, Feed.id == Article.feed_id)
        .where(*in_window)
        .group_by(Article.id, Feed.id)
        .order_by(desc("secs"))
        .limit(TOP_LIMIT)
    )
    top_articles = [
        ActivityArticleOut(article_id=aid, title=title, feed_title=ftitle or furl, seconds=s)
        for aid, title, ftitle, furl, s in article_rows.all()
    ]

    return ActivitySummaryOut(
        range=range_,
        total_seconds=total_seconds,
        prev_total_seconds=prev_total_seconds,
        days=days,
        streak_days=streak_days,
        top_feeds=top_feeds,
        top_articles=top_articles,
    )
