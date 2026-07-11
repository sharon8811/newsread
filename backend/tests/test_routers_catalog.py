import json

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.fetcher import ParsedArticle, ParsedFeed
from app.models import CatalogEntry, CatalogEntryEmbedding
from app.routers import feeds as feeds_router
from app.routers import catalog as catalog_router
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


async def test_inactive_entries_are_hidden_from_browse_and_categories(
    client, users, catalog, session
):
    user = await users.create()
    await catalog(title="Active", category="Tech")
    inactive = await catalog(title="Blocked", category="Hidden")
    inactive.is_active = False
    await session.commit()

    resp = await client.get("/api/catalog", headers=users.auth(user))
    assert [entry["title"] for entry in resp.json()] == ["Active"]
    resp = await client.get("/api/catalog/categories", headers=users.auth(user))
    assert resp.json() == [{"name": "Tech", "count": 1}]


async def test_popular_sort_uses_subscriber_counts(client, users, data, catalog):
    viewer = await users.create()
    reader1 = await users.create()
    reader2 = await users.create()
    quiet = await catalog(url="https://quiet.example/rss", title="Quiet")
    popular = await catalog(url="https://popular.example/rss", title="Popular")
    quiet_feed = await data.feed(url=quiet.url)
    popular_feed = await data.feed(url=popular.url)
    await data.subscribe(reader1, popular_feed)
    await data.subscribe(reader2, popular_feed)
    await data.subscribe(reader1, quiet_feed)

    resp = await client.get("/api/catalog?sort=popular", headers=users.auth(viewer))
    body = resp.json()
    assert [entry["title"] for entry in body] == ["Popular", "Quiet"]
    assert body[0]["subscriber_count"] == 2


async def test_recommended_sort_falls_back_for_new_users(client, users, catalog):
    user = await users.create()
    await catalog(title="Starter feed", description="Useful reading")
    resp = await client.get("/api/catalog?sort=recommended", headers=users.auth(user))
    assert [entry["title"] for entry in resp.json()] == ["Starter feed"]


async def test_semantic_search_ranks_by_catalog_embedding(
    client, users, catalog, session, monkeypatch
):
    user = await users.create()
    climate = await catalog(title="Climate Desk", description="environment reporting")
    chess = await catalog(title="Chess Board", description="tournaments and openings")
    session.add_all([
        CatalogEntryEmbedding(catalog_entry_id=climate.id, model="emb", content_hash="a", embedding=[1.0, 0.0]),
        CatalogEntryEmbedding(catalog_entry_id=chess.id, model="emb", content_hash="b", embedding=[0.0, 1.0]),
    ])
    await session.commit()
    monkeypatch.setattr(catalog_router.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(catalog_router.settings, "openai_embedding_model", "emb")

    async def fake_embed(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(catalog_router.embeddings, "embed_texts", fake_embed)
    resp = await client.get("/api/catalog?q=independent+planet+journalism", headers=users.auth(user))
    assert resp.json()[0]["title"] == "Climate Desk"
    assert resp.json()[0]["match_reason"] == "Semantic match"


async def test_catalog_submission_is_validated(client, users, monkeypatch):
    user = await users.create()

    async def valid_feed(url, *, require_articles=False):
        assert require_articles is True
        return ParsedFeed(
            title="Proposed",
            description="A useful independent publication",
            articles=[ParsedArticle(guid="1", url="https://proposed.example/1", title="One")],
        )

    monkeypatch.setattr(catalog_router, "fetch_feed_data", valid_feed)
    resp = await client.post(
        "/api/catalog/submissions",
        json={"url": "https://proposed.example/rss", "category": "Tech"},
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"



@pytest.fixture
def _mock_refresh(monkeypatch):
    async def fake_refresh(session, feed, *, require_articles=False):
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


async def test_seed_deactivates_removed_managed_entries(session):
    from tests.conftest import engine

    stale = CatalogEntry(
        url="https://removed.example/rss",
        title="Removed",
        description="Was managed",
        category="Tech",
        source="awesome-rss-feeds",
    )
    session.add(stale)
    await session.commit()
    async with engine.begin() as conn:
        await seed_catalog(conn)
    await session.refresh(stale)
    assert stale.is_active is False
