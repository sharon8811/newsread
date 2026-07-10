import json

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models import CatalogEntry
from app.routers import feeds as feeds_router
from app.seeds import CATALOG_SEED_PATH, seed_catalog


@pytest_asyncio.fixture
async def catalog(session):
    """Create catalog entries directly in the DB."""

    async def make(*, url=None, title="A Blog", description=None, site_url=None,
                   category="Tech"):
        count = await session.scalar(select(func.count()).select_from(CatalogEntry))
        entry = CatalogEntry(
            url=url or f"https://catalog{count + 1}.example/rss",
            title=title,
            description=description,
            site_url=site_url,
            category=category,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry

    return make


async def test_browse_requires_auth(client):
    resp = await client.get("/api/catalog")
    assert resp.status_code == 401


async def test_browse_lists_entries_ordered(client, users, catalog):
    user = await users.create()
    await catalog(title="zebra weekly", category="Animals")
    await catalog(title="Ant Digest", category="Animals")
    await catalog(title="Bits", category="Tech")

    resp = await client.get("/api/catalog", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    # Ordered by category, then case-insensitive title.
    assert [(e["category"], e["title"]) for e in body] == [
        ("Animals", "Ant Digest"),
        ("Animals", "zebra weekly"),
        ("Tech", "Bits"),
    ]
    assert all(e["subscribed"] is False and e["feed_id"] is None for e in body)


async def test_browse_search_matches_title_description_category(client, users, catalog):
    user = await users.create()
    await catalog(title="Hacker News", category="Tech")
    await catalog(title="Quiet Blog", description="all about hacking gardens", category="DIY")
    await catalog(title="Chess Daily", category="Chess")

    resp = await client.get("/api/catalog?q=hack", headers=users.auth(user))
    titles = {e["title"] for e in resp.json()}
    assert titles == {"Hacker News", "Quiet Blog"}

    resp = await client.get("/api/catalog?q=chess", headers=users.auth(user))
    assert [e["title"] for e in resp.json()] == ["Chess Daily"]

    resp = await client.get("/api/catalog?q=nomatch", headers=users.auth(user))
    assert resp.json() == []


async def test_browse_category_filter(client, users, catalog):
    user = await users.create()
    await catalog(title="A", category="Tech")
    await catalog(title="B", category="Food")

    resp = await client.get("/api/catalog?category=Food", headers=users.auth(user))
    assert [e["title"] for e in resp.json()] == ["B"]


async def test_browse_marks_subscribed_entries(client, users, data, catalog):
    user = await users.create()
    other = await users.create()
    entry = await catalog(url="https://known.example/rss", title="Known")
    await catalog(title="Not subscribed")

    feed = await data.feed(url=entry.url)
    await data.subscribe(user, feed)

    resp = await client.get("/api/catalog", headers=users.auth(user))
    by_title = {e["title"]: e for e in resp.json()}
    assert by_title["Known"]["subscribed"] is True
    assert by_title["Known"]["feed_id"] == feed.id
    assert by_title["Not subscribed"]["subscribed"] is False

    # Another user's view is unaffected by this user's subscription.
    resp = await client.get("/api/catalog", headers=users.auth(other))
    by_title = {e["title"]: e for e in resp.json()}
    assert by_title["Known"]["subscribed"] is False
    assert by_title["Known"]["feed_id"] is None


async def test_browse_feed_exists_without_subscription(client, users, data, catalog):
    """A Feed row created by someone else doesn't mark the entry subscribed."""
    user = await users.create()
    entry = await catalog(url="https://shared.example/rss")
    await data.feed(url=entry.url)

    resp = await client.get("/api/catalog", headers=users.auth(user))
    assert resp.json()[0]["subscribed"] is False


async def test_categories_with_counts(client, users, catalog):
    user = await users.create()
    await catalog(category="Tech")
    await catalog(category="Tech")
    await catalog(category="Food")

    resp = await client.get("/api/catalog/categories", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == [
        {"name": "Food", "count": 1},
        {"name": "Tech", "count": 2},
    ]


@pytest.fixture
def _mock_refresh(monkeypatch):
    async def fake_refresh(session, feed):
        feed.title = feed.title or "Fetched Title"
        await session.commit()

    monkeypatch.setattr(feeds_router, "refresh_feed", fake_refresh)


async def test_subscribe_from_catalog_via_feeds_endpoint(client, users, catalog, _mock_refresh):
    """The catalog subscribe flow is just POST /feeds with the entry's url."""
    user = await users.create()
    entry = await catalog(url="https://sub.example/rss", title="Subbable")

    resp = await client.post("/api/feeds", json={"url": entry.url}, headers=users.auth(user))
    assert resp.status_code == 201
    feed_id = resp.json()["id"]

    resp = await client.get("/api/catalog", headers=users.auth(user))
    body = resp.json()[0]
    assert body["subscribed"] is True
    assert body["feed_id"] == feed_id


async def test_seed_catalog_idempotent_upsert(session):
    from tests.conftest import engine  # the test engine bound in conftest

    async with engine.begin() as conn:
        await seed_catalog(conn)
    expected = len(json.loads(CATALOG_SEED_PATH.read_text()))
    count = await session.scalar(select(func.count()).select_from(CatalogEntry))
    assert count == expected > 0

    # Local drift gets corrected on the next seed; the row count is stable.
    entry = await session.scalar(select(CatalogEntry).limit(1))
    original_title = entry.title
    entry.title = "locally mangled"
    await session.commit()

    async with engine.begin() as conn:
        await seed_catalog(conn)
    await session.refresh(entry)
    assert entry.title == original_title
    count = await session.scalar(select(func.count()).select_from(CatalogEntry))
    assert count == expected
