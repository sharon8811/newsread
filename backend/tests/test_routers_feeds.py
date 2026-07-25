from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.fetcher import FeedRateLimited
from app.models import Feed, Subscription
from app.routers import feeds as feeds_router


def test_normalize_url():
    assert feeds_router._normalize_url("  example.com/feed ") == "https://example.com/feed"
    assert feeds_router._normalize_url("http://x.com") == "http://x.com"
    assert feeds_router._normalize_url("https://x.com") == "https://x.com"


@pytest.fixture(autouse=True)
def _mock_refresh(monkeypatch):
    """Stub refresh_feed so add/refresh routes don't hit the network."""

    async def fake_refresh(session, feed, *, require_articles=False):
        if not feed.title:
            feed.title = "Fetched Title"
        # A refresh normally inserts articles; add one so listing has data.
        from app.models import Article

        session.add(
            Article(
                feed_id=feed.id,
                guid=f"g-{feed.id}",
                url="https://site/x",
                title="Art",
                excerpt="e",
                content_html="<p>b</p>",
            )
        )
        await session.commit()
        return 1

    monkeypatch.setattr(feeds_router, "refresh_feed", fake_refresh)

    # Autodiscovery only runs after a failed fetch; keep it off the network
    # unless a test opts in.
    async def no_discovery(url, *, require_articles=False):
        return None

    monkeypatch.setattr(feeds_router, "discover_feed_url", no_discovery)
    return fake_refresh


async def test_list_feeds_empty(client, users):
    user = await users.create()
    resp = await client.get("/api/feeds", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_feeds_with_counts(client, users, data):
    user = await users.create()
    feed = await data.feed(title="Tech")
    await data.subscribe(user, feed)
    a1 = await data.article(feed)
    await data.article(feed)
    await data.state(user, a1, is_read=True)

    resp = await client.get("/api/feeds", headers=users.auth(user))
    body = resp.json()
    assert len(body) == 1
    assert body[0]["article_count"] == 2
    assert body[0]["unread_count"] == 1


async def _pending_count(client, users, user):
    resp = await client.get("/api/feeds", headers=users.auth(user))
    return resp.json()[0]["pending_count"]


async def test_pending_count_counts_unstamped_articles(client, users, data):
    user = await users.create()
    feed = await data.feed(title="Tech")
    await data.subscribe(user, feed)
    # Never attempted, full_text empty -> the enrich stage still owes it.
    await data.article(feed)
    # Attempt stamped and too thin to summarize -> settled, even though the
    # image is still missing.
    await data.article(feed, full_text_fetched_at=datetime.now(UTC))
    # Enriched and summarized -> settled.
    await data.article(
        feed,
        full_text="body",
        image_url="https://x/i.png",
        full_text_fetched_at=datetime.now(UTC),
        summary_short="a summary",
    )

    assert await _pending_count(client, users, user) == 1


async def test_pending_count_includes_articles_awaiting_a_summary(client, users, data):
    """The summarize stage runs after enrichment; the indicator has to cover
    it or it reports "done" while the summaries are still being written."""
    user = await users.create()
    feed = await data.feed(title="Tech")
    await data.subscribe(user, feed)
    article = await data.article(
        feed,
        full_text="a long enough body",
        image_url="https://x/i.png",
        full_text_fetched_at=datetime.now(UTC),
    )

    assert await _pending_count(client, users, user) == 1

    article.summary_short = "the summary"
    await data.session.commit()
    assert await _pending_count(client, users, user) == 0


async def test_pending_count_excludes_summaries_the_worker_will_never_write(client, users, data):
    """Skipped and thin-content articles are excluded by the worker's own
    summarize query, so counting them would spin the indicator forever."""
    user = await users.create()
    feed = await data.feed(title="Tech")
    await data.subscribe(user, feed)
    stamped = datetime.now(UTC)
    # Explicitly skipped.
    await data.article(
        feed, full_text="body", full_text_fetched_at=stamped, summary_skipped_reason="too_short"
    )
    # Page fetch failed and the feed's own content is a stub.
    await data.article(feed, full_text="", content_html="<p>stub</p>", full_text_fetched_at=stamped)

    assert await _pending_count(client, users, user) == 0


async def test_pending_count_ignores_summaries_on_ai_disabled_feeds(client, users, data):
    user = await users.create()
    feed = await data.feed(title="Tech", ai_enabled=False)
    await data.subscribe(user, feed)
    await data.article(
        feed,
        full_text="body",
        image_url="https://x/i.png",
        full_text_fetched_at=datetime.now(UTC),
    )

    assert await _pending_count(client, users, user) == 0


async def test_add_new_feed(client, users):
    user = await users.create()
    resp = await client.post(
        "/api/feeds", json={"url": "newfeed.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Fetched Title"
    assert body["url"] == "https://newfeed.example/rss"


async def test_add_feed_creates_subscription(client, users, session):
    user = await users.create()
    await client.post("/api/feeds", json={"url": "sub.example/rss"}, headers=users.auth(user))
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert sub is not None


async def test_add_existing_feed_reuses_it(client, users, data, session):
    user = await users.create()
    await data.feed(url="https://shared.example/rss", title="Shared")
    resp = await client.post(
        "/api/feeds", json={"url": "https://shared.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 201
    feeds_count = len((await session.scalars(select(Feed))).all())
    assert feeds_count == 1


async def test_add_feed_applies_quick_settings(client, users, session):
    user = await users.create()
    resp = await client.post(
        "/api/feeds",
        json={
            "url": "tuned.example/rss",
            "ai_enabled": False,
            "image_gen_enabled": False,
            "is_muted": True,
        },
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ai_enabled"] is False
    assert body["image_gen_enabled"] is False
    assert body["is_muted"] is True

    feed = await session.scalar(select(Feed).where(Feed.id == body["id"]))
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert feed.ai_enabled is False and feed.image_gen_enabled is False
    assert sub.is_muted is True


async def test_add_feed_omitted_settings_keep_existing_values(client, users, data):
    """Subscribing without quick settings must not reset the global switches
    another subscriber already tuned on this feed."""
    user = await users.create()
    await data.feed(
        url="https://tuned.example/rss", title="Tuned", ai_enabled=False, image_gen_enabled=False
    )
    resp = await client.post(
        "/api/feeds", json={"url": "https://tuned.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ai_enabled"] is False
    assert body["image_gen_enabled"] is False
    assert body["is_muted"] is False


async def test_add_feed_already_subscribed(client, users, data):
    user = await users.create()
    feed = await data.feed(url="https://dup.example/rss")
    await data.subscribe(user, feed)
    resp = await client.post(
        "/api/feeds", json={"url": "https://dup.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 201  # idempotent


async def test_add_feed_fetch_failure(client, users, monkeypatch):
    async def boom(session, feed, *, require_articles=False):
        raise RuntimeError("cannot fetch")

    monkeypatch.setattr(feeds_router, "refresh_feed", boom)
    user = await users.create()
    resp = await client.post(
        "/api/feeds", json={"url": "https://broken.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 400


async def test_add_feed_error_names_the_actual_failure(client, users, monkeypatch):
    """ "Could not fetch or parse" for every failure leaves the user with
    nothing to act on: a 404 and an unreachable host need different fixes."""

    async def gone(session, feed, *, require_articles=False):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", feed.url), response=httpx.Response(404)
        )

    monkeypatch.setattr(feeds_router, "refresh_feed", gone)
    user = await users.create()
    resp = await client.post(
        "/api/feeds", json={"url": "https://site.example/nope.xml"}, headers=users.auth(user)
    )
    assert resp.status_code == 400
    assert "404" in resp.json()["detail"]


async def test_add_feed_unreachable_host_names_the_host(client, users, monkeypatch):
    async def unreachable(session, feed, *, require_articles=False):
        raise httpx.ConnectError("nodename nor servname provided")

    monkeypatch.setattr(feeds_router, "refresh_feed", unreachable)
    user = await users.create()
    resp = await client.post(
        "/api/feeds", json={"url": "https://nope.example/rss"}, headers=users.auth(user)
    )
    assert resp.status_code == 400
    assert "nope.example" in resp.json()["detail"]


async def test_add_feed_falls_back_to_autodiscovery(client, users, monkeypatch, session):
    """Pasting the site you're reading is the common case; the subscription
    is keyed on the feed we discover, not on the page URL."""
    tried: list[str] = []

    async def only_the_feed_parses(session_, feed, *, require_articles=False):
        tried.append(feed.url)
        if feed.url != "https://site.example/atom.xml":
            raise ValueError("not a feed")
        feed.title = "The Site"
        return 1

    async def discover(url, *, require_articles=False):
        assert url == "https://site.example"
        return "https://site.example/atom.xml"

    monkeypatch.setattr(feeds_router, "refresh_feed", only_the_feed_parses)
    monkeypatch.setattr(feeds_router, "discover_feed_url", discover)
    user = await users.create()
    resp = await client.post("/api/feeds", json={"url": "site.example"}, headers=users.auth(user))
    assert resp.status_code == 201
    assert resp.json()["url"] == "https://site.example/atom.xml"
    assert tried == ["https://site.example", "https://site.example/atom.xml"]
    # The page URL must not leave a dead feed row behind.
    assert await session.scalar(select(Feed).where(Feed.url == "https://site.example")) is None


async def test_autodiscovery_reuses_an_existing_feed_row(client, users, data, monkeypatch):
    """Two users, one arriving via the site and one via its .xml, share a feed."""
    user = await users.create()
    feed = await data.feed(url="https://site.example/atom.xml", title="The Site")
    await data.article(feed)
    # Read through the ORM before the route's failed first fetch rolls back
    # (and expires) this instance.
    feed_id, feed_url = feed.id, feed.url

    async def boom(session, f, *, require_articles=False):
        raise ValueError("not a feed")

    async def discover(url, *, require_articles=False):
        return feed_url

    monkeypatch.setattr(feeds_router, "refresh_feed", boom)
    monkeypatch.setattr(feeds_router, "discover_feed_url", discover)
    resp = await client.post(
        "/api/feeds", json={"url": "https://site.example"}, headers=users.auth(user)
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == feed_id


async def test_add_feed_rate_limited_subscribes_anyway(client, users, monkeypatch):
    """A 429 from the publisher is not a bad feed: the subscription is created
    with no articles and the poller backfills once the limit clears."""

    async def limited(session, feed, *, require_articles=False):
        raise FeedRateLimited("www.reddit.com")

    monkeypatch.setattr(feeds_router, "refresh_feed", limited)
    user = await users.create()
    resp = await client.post(
        "/api/feeds",
        json={"url": "https://www.reddit.com/r/programming/.rss"},
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["article_count"] == 0
    # No fetched title yet — the URL stands in until the first refresh.
    assert body["title"] == "https://www.reddit.com/r/programming/.rss"
    assert body["last_fetched_at"] is None


async def test_add_existing_empty_feed_rate_limited_subscribes_anyway(
    client, users, data, monkeypatch
):
    user = await users.create()
    feed = await data.feed(url="https://www.reddit.com/r/rust/.rss")

    async def limited(session, feed, *, require_articles=False):
        raise FeedRateLimited("www.reddit.com")

    monkeypatch.setattr(feeds_router, "refresh_feed", limited)
    resp = await client.post("/api/feeds", json={"url": feed.url}, headers=users.auth(user))
    assert resp.status_code == 201


async def test_add_existing_empty_feed_revalidates(client, users, data, monkeypatch):
    """Subscribing to a known feed that has no articles re-fetches it with
    require_articles=True and rejects it when it is still empty."""
    user = await users.create()
    feed = await data.feed(url="https://empty.example/rss")

    async def still_empty(session, feed, *, require_articles=False):
        assert require_articles is True
        raise RuntimeError("no items")

    monkeypatch.setattr(feeds_router, "refresh_feed", still_empty)
    resp = await client.post("/api/feeds", json={"url": feed.url}, headers=users.auth(user))
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]


async def test_refresh_feed_route(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    resp = await client.post(f"/api/feeds/{feed.id}/refresh", headers=users.auth(user))
    assert resp.status_code == 200


async def test_refresh_feed_not_subscribed(client, users, data):
    user = await users.create()
    feed = await data.feed()
    resp = await client.post(f"/api/feeds/{feed.id}/refresh", headers=users.auth(user))
    assert resp.status_code == 404


async def test_refresh_feed_failure(client, users, data, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)

    async def boom(session, feed, *, require_articles=False):
        raise RuntimeError("down")

    monkeypatch.setattr(feeds_router, "refresh_feed", boom)
    resp = await client.post(f"/api/feeds/{feed.id}/refresh", headers=users.auth(user))
    assert resp.status_code == 502


async def test_set_feed_view(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings", json={"view_override": "cards"}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert resp.json()["view_override"] == "cards"


async def test_set_feed_view_clear(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed, view_override="stories")
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings", json={"view_override": None}, headers=users.auth(user)
    )
    assert resp.status_code == 200
    assert resp.json()["view_override"] is None


async def test_settings_not_subscribed(client, users, data):
    user = await users.create()
    feed = await data.feed()
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings", json={"view_override": "cards"}, headers=users.auth(user)
    )
    assert resp.status_code == 404


async def test_settings_empty_patch_rejected(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    resp = await client.patch(f"/api/feeds/{feed.id}/settings", json={}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_settings_subscription_fields(client, users, data):
    user = await users.create()
    feed = await data.feed(title="Original")
    await data.subscribe(user, feed)
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings",
        json={
            "title_override": "  My Name  ",
            "sort_order": "oldest",
            "retention_days": 30,
            "is_muted": True,
        },
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "My Name"  # effective title
    assert body["title_override"] == "My Name"
    assert body["sort_order"] == "oldest"
    assert body["retention_days"] == 30
    assert body["is_muted"] is True


async def test_settings_clear_overrides(client, users, data):
    user = await users.create()
    feed = await data.feed(title="Original")
    await data.subscribe(user, feed)
    await client.patch(
        f"/api/feeds/{feed.id}/settings",
        json={"title_override": "X", "sort_order": "oldest", "retention_days": 7},
        headers=users.auth(user),
    )
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings",
        json={"title_override": "", "sort_order": "newest", "retention_days": None},
        headers=users.auth(user),
    )
    body = resp.json()
    assert body["title"] == "Original"
    assert body["title_override"] is None
    assert body["sort_order"] is None  # "newest" is stored as the default
    assert body["retention_days"] is None


async def test_settings_global_feed_fields(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    resp = await client.patch(
        f"/api/feeds/{feed.id}/settings",
        json={"ai_enabled": False, "image_gen_enabled": False, "refresh_interval_minutes": 60},
        headers=users.auth(user),
    )
    body = resp.json()
    assert body["ai_enabled"] is False
    assert body["image_gen_enabled"] is False
    assert body["refresh_interval_minutes"] == 60
    session.expunge_all()
    stored = await session.get(Feed, feed.id)
    assert stored.ai_enabled is False
    assert stored.image_gen_enabled is False
    assert stored.refresh_interval_minutes == 60


async def test_settings_validation_bounds(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    for payload in (
        {"retention_days": 0},
        {"refresh_interval_minutes": 1},
        {"sort_order": "sideways"},
    ):
        resp = await client.patch(
            f"/api/feeds/{feed.id}/settings", json=payload, headers=users.auth(user)
        )
        assert resp.status_code == 422, payload


async def test_retention_hides_old_articles_from_counts(client, users, data):
    from datetime import datetime, timedelta

    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    now = datetime.now(UTC)
    await data.article(feed, published_at=now - timedelta(days=40))
    await data.article(feed, published_at=now - timedelta(days=1))
    saved_old = await data.article(feed, published_at=now - timedelta(days=40))
    await data.state(user, saved_old, is_saved=True)

    await client.patch(
        f"/api/feeds/{feed.id}/settings", json={"retention_days": 7}, headers=users.auth(user)
    )
    resp = await client.get("/api/feeds", headers=users.auth(user))
    body = resp.json()[0]
    # fresh + saved_old are visible; plain old article is not.
    assert body["article_count"] == 2
    assert body["unread_count"] == 2


async def test_muted_feed_title_sorting_uses_override(client, users, data):
    user = await users.create()
    feed_a = await data.feed(title="AAA")
    feed_z = await data.feed(title="ZZZ")
    await data.subscribe(user, feed_a)
    await data.subscribe(user, feed_z)
    await client.patch(
        f"/api/feeds/{feed_z.id}/settings",
        json={"title_override": "000 First"},
        headers=users.auth(user),
    )
    resp = await client.get("/api/feeds", headers=users.auth(user))
    titles = [f["title"] for f in resp.json()]
    assert titles == ["000 First", "AAA"]


async def test_unsubscribe_deletes_orphan_feed(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    resp = await client.delete(f"/api/feeds/{feed.id}", headers=users.auth(user))
    assert resp.status_code == 204
    session.expunge_all()
    assert await session.get(Feed, feed.id) is None


async def test_unsubscribe_keeps_feed_with_other_subscribers(client, users, data, session):
    u1 = await users.create()
    u2 = await users.create()
    feed = await data.feed()
    await data.subscribe(u1, feed)
    await data.subscribe(u2, feed)
    await client.delete(f"/api/feeds/{feed.id}", headers=users.auth(u1))
    session.expunge_all()
    assert await session.get(Feed, feed.id) is not None


async def test_unsubscribe_keeps_feed_referenced_by_share(client, users, data, session):
    from app.models import Share, ShareRecipient

    u1 = await users.create()
    u2 = await users.create()
    feed = await data.feed()
    await data.subscribe(u1, feed)
    article = await data.article(feed)
    share = Share(from_user_id=u1.id, article_id=article.id)
    share.recipients = [ShareRecipient(to_user_id=u2.id)]
    session.add(share)
    await session.commit()

    await client.delete(f"/api/feeds/{feed.id}", headers=users.auth(u1))
    session.expunge_all()
    assert await session.get(Feed, feed.id) is not None


async def test_unsubscribe_not_subscribed(client, users, data):
    user = await users.create()
    feed = await data.feed()
    resp = await client.delete(f"/api/feeds/{feed.id}", headers=users.auth(user))
    assert resp.status_code == 404


async def test_to_feed_out_title_fallback_to_url():
    feed = Feed(
        id=1,
        url="https://x.com/feed",
        title="",
        site_url=None,
        description=None,
        last_fetched_at=None,
        ai_enabled=True,
        image_gen_enabled=True,
        refresh_interval_minutes=15,
    )
    sub = Subscription(id=1, user_id=1, feed_id=1, is_muted=False)
    out = feeds_router._to_feed_out(feed, 0, 0, 0, sub)
    assert out.title == "https://x.com/feed"
