from datetime import date, timedelta

from sqlalchemy import select

from app.models import ReadingActivity

TODAY = date.today()


async def reader(users, data, *, feed_title="A Feed", feed_url=None):
    """A user subscribed to one feed with one article."""
    user = await users.create()
    feed = await data.feed(title=feed_title, url=feed_url)
    await data.subscribe(user, feed)
    article = await data.article(feed)
    return user, feed, article


def beat(article, seconds=30, source="web", day=TODAY):
    return {
        "article_id": article.id,
        "seconds": seconds,
        "source": source,
        "day": day.isoformat(),
    }


async def log_time(session, user, article, day, seconds, source="web"):
    """Insert activity directly — heartbeats can't reach days outside ±2."""
    session.add(
        ReadingActivity(
            user_id=user.id, article_id=article.id, day=day, source=source, seconds=seconds
        )
    )
    await session.commit()


# --- heartbeat ---


async def test_heartbeat_creates_row(client, users, data, session):
    user, _, article = await reader(users, data)
    resp = await client.post(
        "/api/activity/heartbeat", json=beat(article), headers=users.auth(user)
    )
    assert resp.status_code == 204
    row = await session.scalar(select(ReadingActivity))
    assert (row.user_id, row.article_id, row.seconds, row.source) == (
        user.id,
        article.id,
        30,
        "web",
    )
    assert row.day == TODAY


async def test_heartbeat_increments_existing_row(client, users, data, session):
    user, _, article = await reader(users, data)
    for _ in range(2):
        resp = await client.post(
            "/api/activity/heartbeat", json=beat(article, seconds=25), headers=users.auth(user)
        )
        assert resp.status_code == 204
    rows = (await session.scalars(select(ReadingActivity))).all()
    assert len(rows) == 1
    assert rows[0].seconds == 50


async def test_heartbeat_splits_rows_by_source(client, users, data, session):
    user, _, article = await reader(users, data)
    for source in ("web", "mobile"):
        await client.post(
            "/api/activity/heartbeat", json=beat(article, source=source), headers=users.auth(user)
        )
    rows = (await session.scalars(select(ReadingActivity))).all()
    assert sorted(r.source for r in rows) == ["mobile", "web"]


async def test_heartbeat_article_not_accessible(client, users, data):
    user, _, _ = await reader(users, data)
    stranger_feed = await data.feed()
    foreign_article = await data.article(stranger_feed)
    resp = await client.post(
        "/api/activity/heartbeat", json=beat(foreign_article), headers=users.auth(user)
    )
    assert resp.status_code == 404


async def test_heartbeat_article_missing(client, users, data):
    user, _, article = await reader(users, data)
    payload = beat(article)
    payload["article_id"] = 99999
    resp = await client.post("/api/activity/heartbeat", json=payload, headers=users.auth(user))
    assert resp.status_code == 404


async def test_heartbeat_rejects_out_of_range_seconds(client, users, data):
    user, _, article = await reader(users, data)
    for seconds in (0, 121):
        resp = await client.post(
            "/api/activity/heartbeat", json=beat(article, seconds=seconds), headers=users.auth(user)
        )
        assert resp.status_code == 422


async def test_heartbeat_rejects_bad_source(client, users, data):
    user, _, article = await reader(users, data)
    resp = await client.post(
        "/api/activity/heartbeat",
        json=beat(article, source="carrier-pigeon"),
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_heartbeat_rejects_far_away_day(client, users, data):
    user, _, article = await reader(users, data)
    resp = await client.post(
        "/api/activity/heartbeat",
        json=beat(article, day=TODAY - timedelta(days=10)),
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_heartbeat_requires_auth(client, users, data):
    _, _, article = await reader(users, data)
    resp = await client.post("/api/activity/heartbeat", json=beat(article))
    assert resp.status_code == 401


# --- summary ---


async def test_summary_empty(client, users):
    user = await users.create()
    resp = await client.get("/api/activity/summary", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["range"] == "week"
    assert body["total_seconds"] == 0
    assert body["prev_total_seconds"] == 0
    assert body["streak_days"] == 0
    assert len(body["days"]) == 7
    assert all(d["seconds"] == 0 for d in body["days"])
    assert body["top_feeds"] == []
    assert body["top_articles"] == []


async def test_summary_totals_and_dense_day_series(client, users, data, session):
    user, _, article = await reader(users, data)
    await log_time(session, user, article, TODAY, 120)
    await log_time(session, user, article, TODAY - timedelta(days=2), 60, source="mobile")
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(user)
    )
    body = resp.json()
    assert body["total_seconds"] == 180
    assert len(body["days"]) == 7
    assert body["days"][-1] == {"day": TODAY.isoformat(), "seconds": 120}
    assert body["days"][-3]["seconds"] == 60
    assert body["days"][0]["seconds"] == 0
    # oldest → newest
    assert body["days"][0]["day"] == (TODAY - timedelta(days=6)).isoformat()


async def test_summary_prev_window_powers_delta(client, users, data, session):
    user, _, article = await reader(users, data)
    await log_time(session, user, article, TODAY, 100)
    await log_time(session, user, article, TODAY - timedelta(days=8), 40)  # previous week
    await log_time(session, user, article, TODAY - timedelta(days=20), 999)  # outside both
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(user)
    )
    body = resp.json()
    assert body["total_seconds"] == 100
    assert body["prev_total_seconds"] == 40


async def test_summary_month_range(client, users, data, session):
    user, _, article = await reader(users, data)
    await log_time(session, user, article, TODAY - timedelta(days=15), 60)
    resp = await client.get(
        f"/api/activity/summary?range=month&today={TODAY.isoformat()}", headers=users.auth(user)
    )
    body = resp.json()
    assert body["range"] == "month"
    assert len(body["days"]) == 30
    assert body["total_seconds"] == 60


async def test_summary_rejects_bad_range(client, users):
    user = await users.create()
    resp = await client.get("/api/activity/summary?range=fortnight", headers=users.auth(user))
    assert resp.status_code == 422


async def test_summary_streak_counts_consecutive_days(client, users, data, session):
    user, _, article = await reader(users, data)
    for back in (0, 1, 2, 4):  # gap at day 3 ends the streak
        await log_time(session, user, article, TODAY - timedelta(days=back), 30)
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(user)
    )
    assert resp.json()["streak_days"] == 3


async def test_summary_streak_survives_quiet_today(client, users, data, session):
    user, _, article = await reader(users, data)
    for back in (1, 2):
        await log_time(session, user, article, TODAY - timedelta(days=back), 30)
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(user)
    )
    assert resp.json()["streak_days"] == 2


async def test_summary_top_feeds_and_articles(client, users, data, session):
    user = await users.create()
    titled = await data.feed(title="Titled Feed")
    untitled = await data.feed(title="", url="https://bare.example/rss")
    await data.subscribe(user, titled)
    await data.subscribe(user, untitled)
    long_read = await data.article(titled, title="Long read")
    skim = await data.article(titled, title="Skim")
    bare = await data.article(untitled, title="From bare feed")
    await log_time(session, user, long_read, TODAY, 300)
    await log_time(session, user, skim, TODAY, 50)
    await log_time(session, user, bare, TODAY, 100)
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(user)
    )
    body = resp.json()
    assert [f["seconds"] for f in body["top_feeds"]] == [350, 100]
    assert body["top_feeds"][0]["title"] == "Titled Feed"
    # An untitled feed falls back to its URL.
    assert body["top_feeds"][1]["title"] == "https://bare.example/rss"
    assert [a["title"] for a in body["top_articles"]] == ["Long read", "From bare feed", "Skim"]
    assert body["top_articles"][0]["feed_title"] == "Titled Feed"


async def test_summary_is_scoped_to_current_user(client, users, data, session):
    alice, _, article = await reader(users, data)
    bob = await users.create()
    await log_time(session, alice, article, TODAY, 500)
    resp = await client.get(
        f"/api/activity/summary?today={TODAY.isoformat()}", headers=users.auth(bob)
    )
    assert resp.json()["total_seconds"] == 0
