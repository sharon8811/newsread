"""Daily active-user tracking behind DAU/WAU/MAU trends.

record() is called from the auth dependency on every authenticated request,
so it must cost a dict lookup almost always: one user_activity_days row per
user per UTC day is upserted at most once per THROTTLE window, on its own
short-lived session so read-only requests (which never commit the request
session) still persist it. Reading activity is tracked separately
(reading_activity); this is presence, not engagement.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import db
from .models import UserActivityDay

logger = logging.getLogger(__name__)

THROTTLE = timedelta(hours=1)

# user_id -> (utc day last written, when it was written). In-process only: a
# restart or a second API process just rewrites a row the ON CONFLICT ignores.
_recorded: dict[int, tuple[date, datetime]] = {}


def _should_write(user_id: int, now: datetime) -> bool:
    last = _recorded.get(user_id)
    if last is None:
        return True
    day, written_at = last
    return day != now.date() or now - written_at >= THROTTLE


async def record(user_id: int) -> None:
    """Mark the user active today (UTC). Never raises: activity tracking must
    not be able to fail a request."""
    now = datetime.now(UTC)
    if not _should_write(user_id, now):
        return
    _recorded[user_id] = (now.date(), now)
    try:
        async with db.SessionLocal() as session:
            await session.execute(
                pg_insert(UserActivityDay)
                .values(user_id=user_id, day=now.date())
                .on_conflict_do_nothing(index_elements=["user_id", "day"])
            )
            await session.commit()
    except Exception as exc:
        # Retry on the next request past the throttle window.
        _recorded.pop(user_id, None)
        logger.warning("Recording user %s activity failed: %s", user_id, exc)


def reset() -> None:
    """Clear the throttle cache (tests)."""
    _recorded.clear()
