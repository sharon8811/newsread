from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import embeddings
from app.models import (
    Article,
    ArticleEmbedding,
    ArticleEntity,
    Entity,
    EntitySnapshot,
    UserArticleState,
)
from app.routers import articles as articles_router
from app.routers.articles import _compute_deltas, to_list_item


# --- to_list_item helper ---

def test_to_list_item_enriching_flag():
    art = Article(id=1, feed_id=1, title="T", url="u", comments_url=None, author=None,
                  published_at=None, excerpt="e", image_url=None,
                  full_text_fetched_at=None, full_text="", summary="", summary_short="",
                  summary_medium="")
    item = to_list_item(art, "Feed", None)
    assert item.enriching is True
    assert item.is_read is False
    assert item.is_saved is False


def test_to_list_item_not_enriching_with_image():
    art = Article(id=1, feed_id=1, title="T", url="u", comments_url=None, author=None,
                  published_at=None, excerpt="e", image_url="https://x/i.png",
                  full_text_fetched_at=datetime.now(timezone.utc), full_text="body",
                  summary="", summary_short="", summary_medium="")
    state = UserArticleState(is_read=True, is_saved=True)
    item = to_list_item(art, "Feed", state)
    assert item.enriching is False
    assert item.is_read is True


# --- _compute_deltas ---

def _entity(kind="github", data=None, created_days_ago=30):
    return Entity(
        id=1, kind=kind, canonical_key="a/b", url="u", data=data or {},
        created_at=datetime.now(timezone.utc) - timedelta(days=created_days_ago),
    )


def _snapshot(value, days_ago, metric="stargazers_count"):
    return EntitySnapshot(
        entity_id=1, data={metric: value},
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def test_compute_deltas_no_metric_for_kind():
    assert _compute_deltas(_entity(kind="pypi"), [_snapshot(1, 10)]) == {}


def test_compute_deltas_no_snapshots():
    assert _compute_deltas(_entity(data={"stargazers_count": 100}), []) == {}


def test_compute_deltas_current_not_numeric():
    assert _compute_deltas(_entity(data={"stargazers_count": "x"}), [_snapshot(1, 10)]) == {}


def test_compute_deltas_from_old_snapshot():
    entity = _entity(data={"stargazers_count": 150})
    snapshots = [_snapshot(150, 1), _snapshot(100, 10)]  # newest-first
    assert _compute_deltas(entity, snapshots) == {"stargazers_count_delta_7d": 50}


def test_compute_deltas_baseline_from_oldest_when_entity_old():
    entity = _entity(data={"stargazers_count": 150}, created_days_ago=30)
    snapshots = [_snapshot(120, 1), _snapshot(100, 3)]  # none older than 7d
    # entity created > 7d ago -> baseline is the oldest snapshot (100)
    assert _compute_deltas(entity, snapshots) == {"stargazers_count_delta_7d": 50}


def test_compute_deltas_no_change():
    entity = _entity(data={"stargazers_count": 100})
    assert _compute_deltas(entity, [_snapshot(100, 10)]) == {}


def test_compute_deltas_recent_entity_no_baseline():
    entity = _entity(data={"stargazers_count": 150}, created_days_ago=1)
    snapshots = [_snapshot(120, 1)]  # no snapshot older than 7d, entity is new
    assert _compute_deltas(entity, snapshots) == {}


# --- list / get / state routes ---

async def _setup(users, data, *, subscribe=True):
    user = await users.create()
    feed = await data.feed(title="Tech")
    if subscribe:
        await data.subscribe(user, feed)
    return user, feed


async def test_list_articles_empty(client, users, data):
    user, feed = await _setup(users, data)
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_articles_orders_newest_first(client, users, data):
    user, feed = await _setup(users, data)
    old = await data.article(feed, title="Old",
                             published_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    new = await data.article(feed, title="New",
                             published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", headers=users.auth(user))
    titles = [a["title"] for a in resp.json()]
    assert titles == ["New", "Old"]


async def test_list_articles_filter_unread(client, users, data):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed, title="Read")
    a2 = await data.article(feed, title="Unread")
    await data.state(user, a1, is_read=True)
    resp = await client.get("/api/articles", params={"filter": "unread"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Unread"]


async def test_list_articles_filter_saved(client, users, data):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed, title="Saved")
    await data.article(feed, title="Plain")
    await data.state(user, a1, is_saved=True)
    resp = await client.get("/api/articles", params={"filter": "saved"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Saved"]


async def test_list_articles_by_feed(client, users, data):
    user, feed = await _setup(users, data)
    other = await data.feed(title="Other")
    await data.subscribe(user, other)
    await data.article(feed, title="InFeed")
    await data.article(other, title="InOther")
    resp = await client.get("/api/articles", params={"feed_id": feed.id},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["InFeed"]


async def test_list_articles_sort_oldest_override(client, users, data, session):
    user, feed = await _setup(users, data)
    sub = await session.scalar(select(articles_router.Subscription).where(
        articles_router.Subscription.user_id == user.id))
    sub.sort_order = "oldest"
    await session.commit()
    await data.article(feed, title="Old",
                       published_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    await data.article(feed, title="New",
                       published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", params={"feed_id": feed.id},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Old", "New"]
    # The aggregate inbox ignores per-feed sort and stays newest-first.
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["New", "Old"]


async def test_list_articles_retention_hides_expired(client, users, data, session):
    user, feed = await _setup(users, data)
    sub = await session.scalar(select(articles_router.Subscription).where(
        articles_router.Subscription.user_id == user.id))
    sub.retention_days = 7
    await session.commit()
    now = datetime.now(timezone.utc)
    await data.article(feed, title="Expired", published_at=now - timedelta(days=30))
    await data.article(feed, title="Fresh", published_at=now - timedelta(days=1))
    saved = await data.article(feed, title="SavedOld", published_at=now - timedelta(days=30))
    await data.state(user, saved, is_saved=True)

    resp = await client.get("/api/articles", headers=users.auth(user))
    titles = {a["title"] for a in resp.json()}
    assert titles == {"Fresh", "SavedOld"}


async def test_list_articles_retention_uses_fetched_at_when_unpublished(
    client, users, data, session
):
    user, feed = await _setup(users, data)
    sub = await session.scalar(select(articles_router.Subscription).where(
        articles_router.Subscription.user_id == user.id))
    sub.retention_days = 7
    await session.commit()
    # No published_at: falls back to fetched_at (just inserted → visible).
    await data.article(feed, title="Undated")
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Undated"]


async def test_list_articles_muted_feed_excluded_from_inbox(client, users, data):
    user, feed = await _setup(users, data)
    muted = await data.feed(title="Muted")
    await data.subscribe(user, muted, is_muted=True)
    await data.article(feed, title="Loud")
    quiet = await data.article(muted, title="Quiet")
    await data.state(user, quiet, is_saved=True)

    # Aggregate inbox: muted feed's articles hidden.
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Loud"]
    # The muted feed's own page still shows them.
    resp = await client.get("/api/articles", params={"feed_id": muted.id},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Quiet"]
    # Saved list still includes saved articles from muted feeds.
    resp = await client.get("/api/articles", params={"filter": "saved"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Quiet"]


async def test_list_articles_keyword_fallback(client, users, data, monkeypatch):
    monkeypatch.setattr(embeddings, "is_configured", lambda: False)
    user, feed = await _setup(users, data)
    await data.article(feed, title="Python release", excerpt="news")
    await data.article(feed, title="Rust update", excerpt="news")
    resp = await client.get("/api/articles", params={"q": "python"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Python release"]


async def test_list_articles_pagination(client, users, data):
    user, feed = await _setup(users, data)
    for i in range(5):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", params={"limit": 2, "offset": 0},
                            headers=users.auth(user))
    assert len(resp.json()) == 2


async def test_list_articles_includes_entities(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    entity = Entity(kind="github", canonical_key="a/b", url="https://github.com/a/b",
                    data={"full_name": "a/b", "stargazers_count": 10})
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    await session.commit()
    resp = await client.get("/api/articles", headers=users.auth(user))
    ents = resp.json()[0]["entities"]
    assert ents[0]["kind"] == "github"
    assert ents[0]["badge"]["stars"] == 10


# --- hybrid search (embeddings configured) ---

async def test_list_articles_hybrid_search(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed, title="Neural nets", excerpt="deep learning",
                            summary_medium="about neural networks")
    a2 = await data.article(feed, title="Cooking", excerpt="recipes")

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(articles_router.settings, "openai_embedding_model", "emb")

    async def fake_embed(texts):
        return [[1.0, 0.0, 0.0]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    # give a1 a close vector, a2 a far one
    session.add(ArticleEmbedding(article_id=a1.id, model="emb", embedding=[1.0, 0.0, 0.0]))
    session.add(ArticleEmbedding(article_id=a2.id, model="emb", embedding=[0.0, 1.0, 0.0]))
    await session.commit()

    resp = await client.get("/api/articles", params={"q": "neural"},
                            headers=users.auth(user))
    titles = [a["title"] for a in resp.json()]
    assert titles[0] == "Neural nets"


async def test_list_articles_hybrid_search_scoped_by_feed_and_filter(
    client, users, data, session, monkeypatch
):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed, title="Neural nets unread")
    a2 = await data.article(feed, title="Neural read")
    await data.state(user, a2, is_read=True)

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(articles_router.settings, "openai_embedding_model", "emb")

    async def fake_embed(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    session.add(ArticleEmbedding(article_id=a1.id, model="emb", embedding=[1.0, 0.0]))
    session.add(ArticleEmbedding(article_id=a2.id, model="emb", embedding=[1.0, 0.0]))
    await session.commit()

    resp = await client.get(
        "/api/articles",
        params={"q": "neural", "feed_id": feed.id, "filter": "unread"},
        headers=users.auth(user),
    )
    titles = [a["title"] for a in resp.json()]
    assert titles == ["Neural nets unread"]


async def test_list_articles_hybrid_search_saved_filter(
    client, users, data, session, monkeypatch
):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed, title="Saved neural")
    await data.article(feed, title="Unsaved neural")
    await data.state(user, a1, is_saved=True)

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(articles_router.settings, "openai_embedding_model", "emb")

    async def fake_embed(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    session.add(ArticleEmbedding(article_id=a1.id, model="emb", embedding=[1.0, 0.0]))
    await session.commit()

    resp = await client.get("/api/articles", params={"q": "neural", "filter": "saved"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["Saved neural"]


async def test_list_articles_hybrid_search_embed_failure_falls_back(
    client, users, data, monkeypatch
):
    user, feed = await _setup(users, data)
    await data.article(feed, title="Python news", excerpt="x")

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)

    async def boom(texts):
        raise RuntimeError("embed down")

    monkeypatch.setattr(embeddings, "embed_texts", boom)
    resp = await client.get("/api/articles", params={"q": "python"},
                            headers=users.auth(user))
    assert resp.status_code == 200
    assert [a["title"] for a in resp.json()] == ["Python news"]


async def test_list_articles_hybrid_search_empty_page(client, users, data, monkeypatch):
    user, feed = await _setup(users, data)
    await data.article(feed, title="Something")
    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(articles_router.settings, "openai_embedding_model", "emb")

    async def fake_embed(texts):
        return [[1.0, 0.0]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    # No embeddings rows + a query term absent from tsv -> empty ranked set
    resp = await client.get("/api/articles", params={"q": "zzznomatch", "offset": 100},
                            headers=users.auth(user))
    assert resp.json() == []


# --- get single article ---

async def test_get_article(client, users, data):
    user, feed = await _setup(users, data)
    art = await data.article(feed, content_html="<p>full body</p>")
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["content_html"] == "<p>full body</p>"


async def test_get_article_with_entity_snapshots(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    entity = Entity(kind="github", canonical_key="a/b", url="https://github.com/a/b",
                    data={"full_name": "a/b", "stargazers_count": 200},
                    fetched_at=datetime.now(timezone.utc),
                    created_at=datetime.now(timezone.utc) - timedelta(days=30))
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    session.add(EntitySnapshot(entity_id=entity.id, data={"stargazers_count": 100},
                               captured_at=datetime.now(timezone.utc) - timedelta(days=10)))
    await session.commit()
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    ent = resp.json()["entities"][0]
    assert ent["deltas"] == {"stargazers_count_delta_7d": 100}
    assert len(ent["snapshots"]) == 1


async def test_get_article_not_found(client, users, data):
    user, feed = await _setup(users, data)
    resp = await client.get("/api/articles/99999", headers=users.auth(user))
    assert resp.status_code == 404


async def test_get_article_no_access(client, users, data):
    user, feed = await _setup(users, data, subscribe=False)
    art = await data.article(feed)
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.status_code == 404


async def test_get_article_access_via_share(client, users, data, session):
    from app.models import Share, ShareRecipient

    owner, feed = await _setup(users, data)
    recipient = await users.create(username="recip")
    art = await data.article(feed)
    share = Share(from_user_id=owner.id, article_id=art.id)
    share.recipients = [ShareRecipient(to_user_id=recipient.id)]
    session.add(share)
    await session.commit()
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(recipient))
    assert resp.status_code == 200


# --- set state ---

async def test_set_state_mark_read(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    resp = await client.post(f"/api/articles/{art.id}/state", json={"is_read": True},
                             headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["is_read"] is True


async def test_set_state_upsert_existing(client, users, data):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await data.state(user, art, is_read=True)
    resp = await client.post(f"/api/articles/{art.id}/state", json={"is_saved": True},
                             headers=users.auth(user))
    assert resp.json()["is_saved"] is True
    assert resp.json()["is_read"] is True  # preserved


async def test_set_state_nothing_to_update(client, users, data):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    resp = await client.post(f"/api/articles/{art.id}/state", json={},
                             headers=users.auth(user))
    assert resp.status_code == 422


async def test_set_state_not_found(client, users, data):
    user, feed = await _setup(users, data)
    resp = await client.post("/api/articles/99999/state", json={"is_read": True},
                             headers=users.auth(user))
    assert resp.status_code == 404


# --- mark all read ---

async def test_mark_all_read(client, users, data, session):
    user, feed = await _setup(users, data)
    a1 = await data.article(feed)
    a2 = await data.article(feed)
    resp = await client.post("/api/articles/mark-all-read", json={},
                             headers=users.auth(user))
    assert resp.status_code == 204
    states = (await session.scalars(
        select(UserArticleState).where(UserArticleState.user_id == user.id))).all()
    assert all(s.is_read for s in states)
    assert len(states) == 2


async def test_mark_all_read_by_feed(client, users, data, session):
    user, feed = await _setup(users, data)
    other = await data.feed(title="Other")
    await data.subscribe(user, other)
    a1 = await data.article(feed)
    a2 = await data.article(other)
    await client.post("/api/articles/mark-all-read", json={"feed_id": feed.id},
                      headers=users.auth(user))
    states = (await session.scalars(
        select(UserArticleState).where(UserArticleState.user_id == user.id))).all()
    assert len(states) == 1
    assert states[0].article_id == a1.id


async def test_mark_all_read_nothing(client, users, data):
    user, feed = await _setup(users, data)
    resp = await client.post("/api/articles/mark-all-read", json={},
                             headers=users.auth(user))
    assert resp.status_code == 204


# --- cursor (keyset) pagination ---

async def _walk_pages(client, users, user, limit, extra_params=None):
    """Follow X-Next-Cursor until it disappears; returns titles per page."""
    pages = []
    cursor = None
    for _ in range(20):  # safety bound
        params = {"limit": limit, **(extra_params or {})}
        if cursor:
            params["cursor"] = cursor
        resp = await client.get("/api/articles", params=params, headers=users.auth(user))
        assert resp.status_code == 200
        pages.append([a["title"] for a in resp.json()])
        cursor = resp.headers.get("x-next-cursor")
        if not cursor:
            return pages
    raise AssertionError("pagination never terminated")


async def test_cursor_pagination_walks_all_pages(client, users, data):
    user, feed = await _setup(users, data)
    for i in range(5):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    pages = await _walk_pages(client, users, user, limit=2)
    assert pages == [["A4", "A3"], ["A2", "A1"], ["A0"]]


async def test_cursor_header_absent_when_page_exactly_fits(client, users, data):
    user, feed = await _setup(users, data)
    for i in range(2):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", params={"limit": 2}, headers=users.auth(user))
    assert len(resp.json()) == 2
    assert "x-next-cursor" not in resp.headers


async def test_cursor_stable_when_new_articles_arrive(client, users, data):
    """The property offsets lack: a prepended article must not shift the page."""
    user, feed = await _setup(users, data)
    for i in range(4):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    first = await client.get("/api/articles", params={"limit": 2}, headers=users.auth(user))
    assert [a["title"] for a in first.json()] == ["A3", "A2"]
    cursor = first.headers["x-next-cursor"]

    await data.article(feed, title="Breaking",
                       published_at=datetime(2024, 6, 1, tzinfo=timezone.utc))

    second = await client.get("/api/articles", params={"limit": 2, "cursor": cursor},
                              headers=users.auth(user))
    assert [a["title"] for a in second.json()] == ["A1", "A0"]


async def test_cursor_pagination_null_published_at_tail(client, users, data):
    user, feed = await _setup(users, data)
    await data.article(feed, title="Dated",
                       published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    await data.article(feed, title="Undated1")  # published_at=None
    await data.article(feed, title="Undated2")
    pages = await _walk_pages(client, users, user, limit=1)
    # Dated first, then the null tail by id desc; every article exactly once.
    assert pages == [["Dated"], ["Undated2"], ["Undated1"]]


async def test_cursor_pagination_oldest_sort(client, users, data, session):
    from app.models import Subscription

    user, feed = await _setup(users, data)
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    sub.sort_order = "oldest"
    await session.commit()
    for i in range(3):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    await data.article(feed, title="Undated")
    pages = await _walk_pages(client, users, user, limit=2,
                              extra_params={"feed_id": feed.id})
    assert pages == [["A0", "A1"], ["A2", "Undated"]]


async def test_cursor_rejected_with_search(client, users, data):
    user, feed = await _setup(users, data)
    resp = await client.get("/api/articles", params={"q": "x", "cursor": "abc"},
                            headers=users.auth(user))
    assert resp.status_code == 422


async def test_cursor_invalid_garbage(client, users, data):
    user, feed = await _setup(users, data)
    for bad in ("not-base64!!!", "bm9zZXBhcmF0b3I=", "fA=="):  # garbage, no |, empty id
        resp = await client.get("/api/articles", params={"cursor": bad},
                                headers=users.auth(user))
        assert resp.status_code == 422, bad


async def test_search_pages_have_no_cursor_header(client, users, data):
    user, feed = await _setup(users, data)
    for i in range(3):
        await data.article(feed, title=f"apple {i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", params={"q": "apple", "limit": 2},
                            headers=users.auth(user))
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert "x-next-cursor" not in resp.headers


async def test_cursor_pagination_oldest_sort_null_tail(client, users, data, session):
    from app.models import Subscription

    user, feed = await _setup(users, data)
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    sub.sort_order = "oldest"
    await session.commit()
    await data.article(feed, title="Dated",
                       published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    await data.article(feed, title="Undated1")
    await data.article(feed, title="Undated2")
    pages = await _walk_pages(client, users, user, limit=1,
                              extra_params={"feed_id": feed.id})
    assert pages == [["Dated"], ["Undated1"], ["Undated2"]]


# --- lazy image generation (bring-your-own-key PR 4) ---

from app import image_gen, llm
from app.models import GeneratedImage


def _image_config(user_owned=False):
    return llm.LLMConfig(provider="system", api_key="sk-img", model="img-model",
                         base_url=None, user_owned=user_owned)


def _capture_generation(monkeypatch, config=None):
    """Route the view-trigger at a fake config + generator; returns the capture list."""
    calls = []

    async def fake_resolve(session, user_id):
        return config

    async def fake_generate_for_article(article_id, user_id, cfg, prompt):
        calls.append({"article_id": article_id, "user_id": user_id, "prompt": prompt})

    monkeypatch.setattr(image_gen, "resolve_config", fake_resolve)
    monkeypatch.setattr(image_gen, "generate_for_article", fake_generate_for_article)
    return calls


async def test_get_article_triggers_image_generation(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = None
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["image_pending"] is True
    assert len(calls) == 1
    assert calls[0]["article_id"] == art.id
    # Default template rendered with the article title.
    assert calls[0]["prompt"].startswith(f"{art.title} showcased in a gritty noir")
    await session.refresh(art)
    assert art.image_gen_attempted_at is not None

    # A second view does not double-generate but still reads as pending.
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is True
    assert len(calls) == 1


async def test_get_article_uses_custom_prompt(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = None
    user.image_prompt = "Draw {article_title}, please"
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert calls[0]["prompt"] == f"Draw {art.title}, please"


async def test_get_article_no_trigger_with_existing_image(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = "https://site/og.png"
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert calls == []


async def test_get_article_no_trigger_without_config(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = None
    await session.commit()
    calls = _capture_generation(monkeypatch, config=None)

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert calls == []
    await session.refresh(art)
    assert art.image_gen_attempted_at is None


async def test_get_article_stale_attempt_not_pending(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = None
    art.image_gen_attempted_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert calls == []  # attempt-once policy: no retry


async def test_get_article_broken_key_does_not_block_reading(client, users, data, session, monkeypatch):
    from app import crypto

    user, feed = await _setup(users, data)
    art = await data.article(feed)
    art.image_url = None
    await session.commit()

    async def broken_resolve(session_, user_id):
        raise crypto.TokenCryptoError("key changed")

    monkeypatch.setattr(image_gen, "resolve_config", broken_resolve)
    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["image_pending"] is False


async def test_generated_image_served_unauthenticated(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    session.add(GeneratedImage(article_id=art.id, content_type="image/png",
                               data=b"\x89PNG bytes", model="img-model"))
    await session.commit()

    resp = await client.get(f"/api/articles/{art.id}/generated-image")  # no auth header
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG bytes"
    assert resp.headers["content-type"] == "image/png"
    assert "immutable" in resp.headers["cache-control"]


async def test_generated_image_404_when_missing(client, users, data):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    resp = await client.get(f"/api/articles/{art.id}/generated-image")
    assert resp.status_code == 404


# --- list-triggered generation, per-feed switch, monthly budget ---

async def test_list_triggers_generation_batch(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    for _ in range(6):
        await data.article(feed)
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get("/api/articles", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    # Only a batch of generations starts per response; those articles read as
    # pending, the rest keep their compact no-image state.
    assert len(calls) == articles_router.LIST_GENERATION_BATCH
    pending_ids = {a["id"] for a in body if a["image_pending"]}
    assert pending_ids == {c["article_id"] for c in calls}
    assert len(body) == 6
    # Claims are attributed to the requesting user for the monthly budget.
    attributed = (await session.scalars(
        select(Article).where(Article.image_gen_user_id == user.id)
    )).all()
    assert {a.id for a in attributed} == pending_ids

    # While the batch is in flight, another poll starts nothing new.
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert len(calls) == articles_router.LIST_GENERATION_BATCH
    assert {a["id"] for a in resp.json() if a["image_pending"]} == pending_ids


async def test_list_no_generation_without_config(client, users, data, monkeypatch):
    user, feed = await _setup(users, data)
    await data.article(feed)
    calls = _capture_generation(monkeypatch, config=None)

    resp = await client.get("/api/articles", headers=users.auth(user))
    assert calls == []
    assert all(a["image_pending"] is False for a in resp.json())


async def test_feed_toggle_blocks_list_generation(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    await data.article(feed)
    feed.image_gen_enabled = False
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get("/api/articles", headers=users.auth(user))
    assert calls == []
    assert all(a["image_pending"] is False for a in resp.json())


async def test_feed_toggle_blocks_detail_generation(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    feed.image_gen_enabled = False
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert calls == []


async def test_monthly_budget_caps_list_generation(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    for _ in range(4):
        await data.article(feed)
    user.image_gen_monthly_limit = 2
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get("/api/articles", headers=users.auth(user))
    assert len(calls) == 2
    assert sum(a["image_pending"] for a in resp.json()) == 2

    # The budget is spent by the claims: nothing more starts, on lists...
    resp = await client.get("/api/articles", headers=users.auth(user))
    assert len(calls) == 2
    # ...or on the article view.
    unclaimed = next(a for a in resp.json() if not a["image_pending"])
    resp = await client.get(f"/api/articles/{unclaimed['id']}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert len(calls) == 2


async def test_monthly_budget_counts_current_month_only(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    # A claim from a previous month must not count against this month.
    old = await data.article(feed, image_url="https://x/old.png")
    old.image_gen_attempted_at = datetime.now(timezone.utc) - timedelta(days=40)
    old.image_gen_user_id = user.id
    fresh = await data.article(feed)
    user.image_gen_monthly_limit = 1
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{fresh.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is True
    assert len(calls) == 1


async def test_zero_budget_blocks_generation(client, users, data, session, monkeypatch):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    user.image_gen_monthly_limit = 0
    await session.commit()
    calls = _capture_generation(monkeypatch, _image_config())

    resp = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert resp.json()["image_pending"] is False
    assert calls == []


# --- suppressions (not interested) ---

async def _suppress(session, user, article):
    from app.models import ArticleSuppression, UserDislikeRule

    rule = UserDislikeRule(user_id=user.id, kind="article", article_id=article.id, label=article.title)
    session.add(rule)
    await session.commit()
    session.add(ArticleSuppression(user_id=user.id, article_id=article.id, rule_id=rule.id))
    await session.commit()
    return rule


async def test_suppressed_article_hidden_from_lists(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    hidden = await data.article(feed, title="Hidden")
    visible = await data.article(feed, title="Visible")
    await _suppress(session, user, hidden)

    for filter_ in ("all", "unread"):
        resp = await client.get(f"/api/articles?filter={filter_}", headers=users.auth(user))
        ids = [a["id"] for a in resp.json()]
        assert hidden.id not in ids and visible.id in ids

    # ILIKE search respects the suppression too.
    resp = await client.get("/api/articles?q=Hidden", headers=users.auth(user))
    assert resp.json() == []


async def test_suppressed_article_still_in_saved_and_detail(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, title="Kept")
    await data.state(user, art, is_saved=True)
    await _suppress(session, user, art)

    resp = await client.get("/api/articles?filter=saved", headers=users.auth(user))
    assert [a["id"] for a in resp.json()] == [art.id]
    # Soft-hide: the detail view is the escape hatch.
    detail = await client.get(f"/api/articles/{art.id}", headers=users.auth(user))
    assert detail.status_code == 200


async def test_mark_all_read_skips_suppressed(client, users, data, session):
    from app.models import UserArticleState

    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    hidden = await data.article(feed)
    visible = await data.article(feed)
    await _suppress(session, user, hidden)

    resp = await client.post("/api/articles/mark-all-read", json={}, headers=users.auth(user))
    assert resp.status_code == 204
    read_ids = set(await session.scalars(
        select(UserArticleState.article_id).where(
            UserArticleState.user_id == user.id, UserArticleState.is_read.is_(True)
        )
    ))
    assert read_ids == {visible.id}


# --- GET /articles/{id}/related ---

async def _related_embed(session, article, vector, *, model="test-model"):
    session.add(ArticleEmbedding(article_id=article.id, model=model, embedding=vector))
    await session.commit()


def _configure_related(monkeypatch, *, model="test-model"):
    monkeypatch.setattr(articles_router.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(articles_router.settings, "openai_embedding_model", model)


async def test_related_inaccessible_article(client, users, data):
    user = await users.create()
    feed = await data.feed()
    art = await data.article(feed)  # not subscribed
    resp = await client.get(f"/api/articles/{art.id}/related", headers=users.auth(user))
    assert resp.status_code == 404


async def test_related_vector_tiers_ordering_and_ceiling(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    source = await data.article(feed, title="Source")
    dupe = await data.article(feed, title="Near duplicate")
    topical = await data.article(feed, title="Same topic")
    unrelated = await data.article(feed, title="Unrelated")
    bare = await data.article(feed, title="No embedding")
    await _related_embed(session, source, [1.0, 0.0, 0.0])
    await _related_embed(session, dupe, [0.999, 0.04, 0.0])   # distance ~0.001
    await _related_embed(session, topical, [0.5, 0.866, 0.0])  # distance ~0.5
    await _related_embed(session, unrelated, [0.0, 1.0, 0.0]) # distance 1.0 > ceiling
    _configure_related(monkeypatch)

    resp = await client.get(f"/api/articles/{source.id}/related", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert [item["id"] for item in body] == [dupe.id, topical.id]  # by distance, self excluded
    assert [item["tier"] for item in body] == ["same_story", "related"]
    assert body[0]["feed_title"] == "A Feed"
    assert unrelated.id not in [item["id"] for item in body]
    assert bare.id not in [item["id"] for item in body]


async def test_related_scope_exclusions(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    muted_feed = await data.feed(title="Muted")
    await data.subscribe(user, muted_feed, is_muted=True)
    other_feed = await data.feed(title="Unsubscribed")

    source = await data.article(feed, title="Source")
    ok = await data.article(feed, title="Visible")
    muted = await data.article(muted_feed, title="On muted feed")
    foreign = await data.article(other_feed, title="Not subscribed")
    old = await data.article(feed, title="Ancient")
    suppressed = await data.article(feed, title="Suppressed")
    stale_model = await data.article(feed, title="Stale model")

    close = [0.99, 0.1, 0.0]
    await _related_embed(session, source, [1.0, 0.0, 0.0])
    for candidate in (ok, muted, foreign, old, suppressed):
        await _related_embed(session, candidate, close)
    await _related_embed(session, stale_model, close, model="legacy")

    old.fetched_at = datetime.now(timezone.utc) - timedelta(days=120)
    await session.commit()
    await _suppress(session, user, suppressed)
    _configure_related(monkeypatch)

    resp = await client.get(f"/api/articles/{source.id}/related", headers=users.auth(user))
    assert [item["id"] for item in resp.json()] == [ok.id]


async def test_related_is_read_mapping_and_limit(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    source = await data.article(feed)
    await _related_embed(session, source, [1.0, 0.0, 0.0])
    candidates = []
    for i in range(6):
        art = await data.article(feed, title=f"Cand {i}")
        await _related_embed(session, art, [1.0, 0.001 * (i + 1), 0.0])
        candidates.append(art)
    await data.state(user, candidates[0], is_read=True)
    _configure_related(monkeypatch)

    resp = await client.get(f"/api/articles/{source.id}/related", headers=users.auth(user))
    body = resp.json()
    assert len(body) == 5  # RELATED_LIMIT
    assert body[0]["id"] == candidates[0].id and body[0]["is_read"] is True
    assert body[1]["is_read"] is False


async def test_related_entity_fallback(client, users, data, session, monkeypatch):
    from app.models import ArticleEntity, Entity

    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    source = await data.article(feed, title="Source")
    linked_new = await data.article(feed, title="Linked newer")
    linked_old = await data.article(feed, title="Linked older")
    unlinked = await data.article(feed, title="Unlinked")
    # Source has an embedding, but under a stale model -> vector leg skipped.
    await _related_embed(session, source, [1.0, 0.0, 0.0], model="legacy")
    _configure_related(monkeypatch)

    entity = Entity(kind="github", canonical_key="acme/x", url="https://gh/acme/x")
    session.add(entity)
    await session.commit()
    for art in (source, linked_new, linked_old):
        session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary"))
    linked_new.published_at = datetime.now(timezone.utc)
    linked_old.published_at = datetime.now(timezone.utc) - timedelta(days=3)
    await session.commit()

    resp = await client.get(f"/api/articles/{source.id}/related", headers=users.auth(user))
    body = resp.json()
    assert [item["id"] for item in body] == [linked_new.id, linked_old.id]  # newest first
    assert all(item["tier"] == "related" for item in body)
    assert unlinked.id not in [item["id"] for item in body]


async def test_related_empty_without_embeddings_or_entities(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    source = await data.article(feed)
    await data.article(feed, title="Other")
    resp = await client.get(f"/api/articles/{source.id}/related", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json() == []


# --- scroll auto-read: batch state, provenance, anchor + backward paging ---

async def _dated_articles(data, feed, count):
    """count articles titled A0..A(n-1), published a day apart (A0 oldest)."""
    return [
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
        for i in range(count)
    ]


async def _states(session, user):
    rows = (await session.scalars(
        select(UserArticleState).where(UserArticleState.user_id == user.id)
    )).all()
    return {s.article_id: s for s in rows}


async def test_state_batch_marks_read_with_provenance(client, users, data, session):
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 3)
    resp = await client.post("/api/articles/state/batch",
                             json={"article_ids": [a.id for a in arts[:2]]},
                             headers=users.auth(user))
    assert resp.status_code == 204
    states = await _states(session, user)
    assert states[arts[0].id].is_read and states[arts[1].id].is_read
    assert states[arts[0].id].read_source == "scrolled"
    assert states[arts[0].id].read_at is not None
    assert arts[2].id not in states


async def test_state_batch_ignores_unsubscribed_articles(client, users, data, session):
    user, feed = await _setup(users, data)
    mine = await data.article(feed, title="Mine")
    other_feed = await data.feed(title="NotSubscribed")
    foreign = await data.article(other_feed, title="Foreign")
    resp = await client.post("/api/articles/state/batch",
                             json={"article_ids": [mine.id, foreign.id]},
                             headers=users.auth(user))
    assert resp.status_code == 204
    states = await _states(session, user)
    assert states[mine.id].is_read
    assert foreign.id not in states


async def test_state_batch_preserves_first_read_provenance(client, users, data, session):
    """A scroll flush over an already-opened article must not downgrade it."""
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    opened = await client.post(f"/api/articles/{art.id}/state",
                               json={"is_read": True}, headers=users.auth(user))
    assert opened.status_code == 200
    resp = await client.post("/api/articles/state/batch",
                             json={"article_ids": [art.id]}, headers=users.auth(user))
    assert resp.status_code == 204
    states = await _states(session, user)
    assert states[art.id].read_source == "opened"


async def test_state_batch_unread_clears_provenance(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await client.post("/api/articles/state/batch",
                      json={"article_ids": [art.id]}, headers=users.auth(user))
    resp = await client.post("/api/articles/state/batch",
                             json={"article_ids": [art.id], "is_read": False},
                             headers=users.auth(user))
    assert resp.status_code == 204
    states = await _states(session, user)
    assert states[art.id].is_read is False
    assert states[art.id].read_at is None
    assert states[art.id].read_source is None


async def test_state_batch_keeps_saved_flag(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await data.state(user, art, is_saved=True)
    await client.post("/api/articles/state/batch",
                      json={"article_ids": [art.id]}, headers=users.auth(user))
    states = await _states(session, user)
    assert states[art.id].is_saved is True
    assert states[art.id].is_read is True


async def test_state_batch_empty_ids_rejected(client, users, data):
    user, _ = await _setup(users, data)
    resp = await client.post("/api/articles/state/batch",
                             json={"article_ids": []}, headers=users.auth(user))
    assert resp.status_code == 422


async def test_set_state_records_opened_source(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await client.post(f"/api/articles/{art.id}/state",
                      json={"is_read": True}, headers=users.auth(user))
    states = await _states(session, user)
    assert states[art.id].read_source == "opened"
    assert states[art.id].read_at is not None


async def test_set_state_explicit_source_story(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await client.post(f"/api/articles/{art.id}/state",
                      json={"is_read": True, "read_source": "story"},
                      headers=users.auth(user))
    states = await _states(session, user)
    assert states[art.id].read_source == "story"


async def test_set_state_unread_clears_provenance(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    await client.post(f"/api/articles/{art.id}/state",
                      json={"is_read": True}, headers=users.auth(user))
    await client.post(f"/api/articles/{art.id}/state",
                      json={"is_read": False}, headers=users.auth(user))
    states = await _states(session, user)
    assert states[art.id].read_at is None
    assert states[art.id].read_source is None


async def test_mark_all_read_records_source(client, users, data, session):
    user, feed = await _setup(users, data)
    art = await data.article(feed)
    resp = await client.post("/api/articles/mark-all-read", json={},
                             headers=users.auth(user))
    assert resp.status_code == 204
    states = await _states(session, user)
    assert states[art.id].read_source == "mark_all"


async def test_anchor_starts_at_first_unread(client, users, data, session):
    """Newest-first list A4..A0 with A4, A3 read: page starts at A2, prev
    cursor points back at the read history, unread count reported."""
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 5)
    await data.state(user, arts[4], is_read=True)
    await data.state(user, arts[3], is_read=True)
    resp = await client.get("/api/articles",
                            params={"anchor": "resume", "limit": 2},
                            headers=users.auth(user))
    assert resp.status_code == 200
    assert [a["title"] for a in resp.json()] == ["A2", "A1"]
    assert resp.headers["x-unread-count"] == "3"
    assert "x-prev-cursor" in resp.headers
    assert "x-next-cursor" in resp.headers

    # The prev cursor pages backward through the read history, in list order.
    back = await client.get("/api/articles",
                            params={"cursor": resp.headers["x-prev-cursor"],
                                    "direction": "before", "limit": 5},
                            headers=users.auth(user))
    assert [a["title"] for a in back.json()] == ["A4", "A3"]
    assert "x-prev-cursor" not in back.headers  # nothing earlier


async def test_anchor_at_top_has_no_prev_cursor(client, users, data):
    user, feed = await _setup(users, data)
    await _dated_articles(data, feed, 2)
    resp = await client.get("/api/articles", params={"anchor": "resume"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["A1", "A0"]
    assert resp.headers["x-unread-count"] == "2"
    assert "x-prev-cursor" not in resp.headers


async def test_anchor_all_read_falls_back_to_top(client, users, data):
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 2)
    for art in arts:
        await data.state(user, art, is_read=True)
    resp = await client.get("/api/articles", params={"anchor": "resume"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["A1", "A0"]
    assert resp.headers["x-unread-count"] == "0"


async def test_anchor_respects_oldest_sort(client, users, data, session):
    from app.models import Subscription

    user, feed = await _setup(users, data)
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    sub.sort_order = "oldest"
    await session.commit()
    arts = await _dated_articles(data, feed, 3)
    await data.state(user, arts[0], is_read=True)  # oldest read
    resp = await client.get("/api/articles",
                            params={"anchor": "resume", "feed_id": feed.id},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()] == ["A1", "A2"]
    assert "x-prev-cursor" in resp.headers


async def test_anchor_rejected_with_cursor_or_q(client, users, data):
    user, feed = await _setup(users, data)
    for i in range(2):
        await data.article(feed, title=f"A{i}",
                           published_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc))
    first = await client.get("/api/articles", params={"limit": 1}, headers=users.auth(user))
    cursor = first.headers["x-next-cursor"]
    with_q = await client.get("/api/articles",
                              params={"anchor": "resume", "q": "x"},
                              headers=users.auth(user))
    assert with_q.status_code == 422
    with_cursor = await client.get("/api/articles",
                                   params={"anchor": "resume", "cursor": cursor},
                                   headers=users.auth(user))
    assert with_cursor.status_code == 422


async def test_direction_before_requires_cursor(client, users, data):
    user, _ = await _setup(users, data)
    resp = await client.get("/api/articles", params={"direction": "before"},
                            headers=users.auth(user))
    assert resp.status_code == 422


async def test_direction_before_walks_history_in_pages(client, users, data):
    """Backward pages return blocks in list order and chain via X-Prev-Cursor."""
    user, feed = await _setup(users, data)
    await _dated_articles(data, feed, 5)
    # Cursor at A2, the last row of a 3-row first page (A4, A3, A2).
    first = await client.get("/api/articles", params={"limit": 3},
                             headers=users.auth(user))
    cursor = first.headers["x-next-cursor"]

    back1 = await client.get("/api/articles",
                             params={"cursor": cursor, "direction": "before", "limit": 1},
                             headers=users.auth(user))
    assert [a["title"] for a in back1.json()] == ["A3"]  # row just above A2
    back2 = await client.get("/api/articles",
                             params={"cursor": back1.headers["x-prev-cursor"],
                                     "direction": "before", "limit": 5},
                             headers=users.auth(user))
    assert [a["title"] for a in back2.json()] == ["A4"]
    assert "x-prev-cursor" not in back2.headers


async def test_unread_reading_window_keeps_read_history_for_back_paging(
    client, users, data
):
    """An unread list can prepend rows read earlier in the same session."""
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 5)
    await data.state(user, arts[4], is_read=True)
    await data.state(user, arts[3], is_read=True)

    anchor = await client.get(
        "/api/articles",
        params={"filter": "unread", "anchor": "resume", "reading_window": True},
        headers=users.auth(user),
    )
    assert [article["title"] for article in anchor.json()] == ["A2", "A1", "A0"]
    assert "x-prev-cursor" in anchor.headers

    history = await client.get(
        "/api/articles",
        params={
            "filter": "unread",
            "cursor": anchor.headers["x-prev-cursor"],
            "direction": "before",
            "reading_window": True,
            "limit": 2,
        },
        headers=users.auth(user),
    )
    assert [article["title"] for article in history.json()] == ["A4", "A3"]
    assert all(article["is_read"] for article in history.json())


async def test_direction_before_null_published_tail(client, users, data):
    user, feed = await _setup(users, data)
    await data.article(feed, title="Dated",
                       published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    await data.article(feed, title="Undated1")
    await data.article(feed, title="Undated2")
    # Walk forward to the last row (Undated1), then read everything before it.
    pages = await _walk_pages(client, users, user, limit=1)
    assert pages == [["Dated"], ["Undated2"], ["Undated1"]]
    second = await client.get("/api/articles", params={"limit": 2},
                              headers=users.auth(user))
    cursor = second.headers["x-next-cursor"]  # cursor at Undated2 (in the null tail)
    back = await client.get("/api/articles",
                            params={"cursor": cursor, "direction": "before", "limit": 5},
                            headers=users.auth(user))
    # Exclusive of the cursor row: only the dated head lies before the tail.
    assert [a["title"] for a in back.json()] == ["Dated"]


# --- reading frontier (resume position) ---

async def test_batch_frontier_moves_resume_past_new_arrivals(client, users, data):
    """The Telegram property: after scrolling past A2..A0 (frontier = A0),
    newly arrived articles above must not teleport resume back to the top —
    they're reported in X-New-Above-Count instead."""
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 3)  # list order A2, A1, A0
    resp = await client.post(
        "/api/articles/state/batch",
        json={"article_ids": [arts[2].id, arts[1].id],
              "frontier_article_id": arts[1].id},
        headers=users.auth(user))
    assert resp.status_code == 204

    # Two new articles arrive above everything.
    await data.article(feed, title="N0",
                       published_at=datetime(2024, 2, 1, tzinfo=timezone.utc))
    await data.article(feed, title="N1",
                       published_at=datetime(2024, 2, 2, tzinfo=timezone.utc))

    resp = await client.get("/api/articles", params={"anchor": "resume"},
                            headers=users.auth(user))
    # Resume = first unread at/after the frontier (A1) -> A0, not N1.
    assert [a["title"] for a in resp.json()][0] == "A0"
    assert resp.headers["x-unread-count"] == "3"  # A0 + the two new ones
    assert resp.headers["x-new-above-count"] == "2"
    assert "x-prev-cursor" in resp.headers


async def test_resume_falls_back_to_first_unread_when_caught_up_below(client, users, data):
    """Everything at/after the frontier read -> resume at the newest unread."""
    user, feed = await _setup(users, data)
    arts = await _dated_articles(data, feed, 2)
    await client.post(
        "/api/articles/state/batch",
        json={"article_ids": [a.id for a in arts],
              "frontier_article_id": arts[0].id},
        headers=users.auth(user))
    fresh = await data.article(feed, title="Fresh",
                               published_at=datetime(2024, 3, 1, tzinfo=timezone.utc))
    resp = await client.get("/api/articles", params={"anchor": "resume"},
                            headers=users.auth(user))
    assert [a["title"] for a in resp.json()][0] == "Fresh"
    assert resp.headers["x-new-above-count"] == "0"


async def test_batch_frontier_scoped_per_feed(client, users, data, session):
    from app.models import UserReadingPosition

    user, feed = await _setup(users, data)
    art = await data.article(feed,
                             published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    await client.post(
        "/api/articles/state/batch",
        json={"article_ids": [art.id], "frontier_article_id": art.id,
              "frontier_feed_id": feed.id},
        headers=users.auth(user))
    positions = (await session.scalars(
        select(UserReadingPosition).where(UserReadingPosition.user_id == user.id)
    )).all()
    assert [p.scope for p in positions] == [f"feed:{feed.id}"]
    assert positions[0].article_id == art.id


async def test_batch_frontier_ignored_when_unsubscribed(client, users, data, session):
    from app.models import UserReadingPosition

    user, feed = await _setup(users, data)
    mine = await data.article(feed)
    other_feed = await data.feed(title="NotMine")
    foreign = await data.article(other_feed)
    await client.post(
        "/api/articles/state/batch",
        json={"article_ids": [mine.id], "frontier_article_id": foreign.id},
        headers=users.auth(user))
    positions = (await session.scalars(
        select(UserReadingPosition).where(UserReadingPosition.user_id == user.id)
    )).all()
    assert positions == []
