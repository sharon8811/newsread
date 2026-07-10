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
