"""ARQ worker: polls due feeds on a schedule.

Run with: arq app.worker.WorkerSettings
"""

import logging
from datetime import datetime, timedelta, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from .config import settings
from .db import SessionLocal, init_db
from .fetcher import refresh_feed
from .models import Feed

logger = logging.getLogger(__name__)


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


async def startup(ctx: dict) -> None:
    await init_db()
    logger.info("Feed-polling worker started")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    functions: list = []
    cron_jobs = [cron(poll_feeds, minute=set(range(0, 60, 3)), run_at_startup=True)]
