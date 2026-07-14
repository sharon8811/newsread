from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import llm
from app.models import (
    ArticleEmbedding,
    ArticleEntity,
    ArticleSuppression,
    DislikeRuleEmbedding,
    Entity,
)
from app.routers import interests


async def _entity(session, *, kind="github", key="acme/widget", data=None):
    entity = Entity(kind=kind, canonical_key=key, url=f"https://x/{key}", data=data or {})
    session.add(entity)
    await session.commit()
    await session.refresh(entity)
    return entity


async def _link(session, article, entity, *, source="primary"):
    session.add(ArticleEntity(article_id=article.id, entity_id=entity.id, source=source))
    await session.commit()


async def _embed(session, article, vector, *, model="test-model"):
    session.add(ArticleEmbedding(article_id=article.id, model=model, embedding=vector))
    await session.commit()


def _configure_embeddings(monkeypatch, *, model="test-model"):
    monkeypatch.setattr(interests.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(interests.settings, "openai_embedding_model", model)


# --- GET /interests/dislike-options/{id} ---


async def test_options_article_not_accessible(client, users, data):
    user = await users.create()
    feed = await data.feed()
    art = await data.article(feed)  # user not subscribed
    resp = await client.get(f"/api/interests/dislike-options/{art.id}", headers=users.auth(user))
    assert resp.status_code == 404


async def test_options_degrades_without_llm_and_embeddings(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    entity = await _entity(session, data={"full_name": "acme/widget"})
    await _link(session, art, entity)

    resp = await client.get(f"/api/interests/dislike-options/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["topics"] == []
    assert body["story_available"] is False
    assert [e["entity_id"] for e in body["entities"]] == [entity.id]
    assert body["entities"][0]["label"] == "acme/widget"


async def test_options_with_topics_and_story(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, summary_medium="a summary")
    await _embed(session, art, [1.0, 0.0, 0.0])
    _configure_embeddings(monkeypatch)
    monkeypatch.setattr(
        interests.llm,
        "system_config",
        lambda: llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m"),
    )

    async def fake_resolve(session_, user_id):
        return llm.system_config()

    captured = {}

    async def fake_topics(title, summary, *, config=None, usage=None):
        captured["args"] = (title, summary)
        return ["celebrity gossip", "crypto prices"]

    monkeypatch.setattr(interests.llm, "resolve_config", fake_resolve)
    monkeypatch.setattr(interests.llm, "dislike_topics", fake_topics)

    resp = await client.get(f"/api/interests/dislike-options/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["topics"] == ["celebrity gossip", "crypto prices"]
    assert body["story_available"] is True
    assert captured["args"] == (art.title, "a summary")


async def test_options_llm_failure_still_200(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    _configure_embeddings(monkeypatch)

    async def fake_resolve(session_, user_id):
        return llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")

    async def boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(interests.llm, "resolve_config", fake_resolve)
    monkeypatch.setattr(interests.llm, "dislike_topics", boom)

    resp = await client.get(f"/api/interests/dislike-options/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["topics"] == []


# --- POST /interests/dislikes ---


async def test_create_article_rule_hides_article(client, users, data, session):
    user = await users.create()
    other = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    await data.subscribe(other, feed)
    art = await data.article(feed, title="Boring")

    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["kind"] == "article"
    assert body["rule"]["label"] == "Boring"
    assert body["rule"]["hidden_count"] == 1
    assert body["preview"] == [{"id": art.id, "title": "Boring"}]

    listing = await client.get("/api/articles", headers=users.auth(user))
    assert art.id not in [a["id"] for a in listing.json()]
    # The other subscriber still sees it.
    listing_other = await client.get("/api/articles", headers=users.auth(other))
    assert art.id in [a["id"] for a in listing_other.json()]


async def test_create_article_rule_is_idempotent(client, users, data):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)

    first = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    second = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    assert second.json()["rule"]["id"] == first.json()["rule"]["id"]


async def test_create_entity_rule_backfills_recent_only(client, users, data, session):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    entity = await _entity(session)
    linked = await data.article(feed, title="Linked")
    unlinked = await data.article(feed, title="Unlinked")
    old = await data.article(feed, title="Old")
    await _link(session, linked, entity)
    await _link(session, old, entity)
    old.fetched_at = datetime.now(UTC) - timedelta(days=40)
    await session.commit()

    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "entity", "entity_id": entity.id},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["label"] == "acme/widget"
    assert body["rule"]["hidden_count"] == 1
    assert [p["id"] for p in body["preview"]] == [linked.id]

    listing = await client.get("/api/articles", headers=users.auth(user))
    ids = [a["id"] for a in listing.json()]
    assert linked.id not in ids
    assert unlinked.id in ids
    assert old.id in ids  # outside the backfill window


async def test_create_entity_rule_missing_entity(client, users):
    user = await users.create()
    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "entity", "entity_id": 424242},
        headers=users.auth(user),
    )
    assert resp.status_code == 404


async def test_create_story_rule(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, title="The Story")
    similar = await data.article(feed, title="The Story, continued")
    unrelated = await data.article(feed, title="Else")
    await _embed(session, art, [1.0, 0.0, 0.0])
    await _embed(session, similar, [0.99, 0.1, 0.0])
    await _embed(session, unrelated, [0.0, 1.0, 0.0])
    _configure_embeddings(monkeypatch)

    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "story", "article_id": art.id},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["expires_at"] is not None
    assert body["rule"]["hidden_count"] == 2  # the article itself + the near-duplicate
    hidden_ids = {p["id"] for p in body["preview"]}
    assert hidden_ids == {art.id, similar.id}

    # The rule embedding is a snapshot copy of the article's vector.
    rule_emb = await session.scalar(select(DislikeRuleEmbedding))
    assert rule_emb.model == "test-model"


async def test_create_story_rule_unembedded_article(client, users, data, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    _configure_embeddings(monkeypatch)

    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "story", "article_id": art.id},
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_create_topic_rule(client, users, data, session, monkeypatch):
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    crypto_art = await data.article(feed, title="BTC pumps")
    other_art = await data.article(feed, title="Gardening tips")
    await _embed(session, crypto_art, [1.0, 0.0, 0.0])
    await _embed(session, other_art, [0.0, 1.0, 0.0])
    _configure_embeddings(monkeypatch)

    async def fake_embed_texts(texts):
        assert texts == ["crypto prices"]
        return [[0.95, 0.05, 0.0]]

    monkeypatch.setattr(interests.embeddings, "embed_texts", fake_embed_texts)

    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "topic", "phrase": "  crypto   prices "},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["rule"]["phrase"] == "crypto prices"
    assert body["rule"]["hidden_count"] == 1
    assert [p["id"] for p in body["preview"]] == [crypto_art.id]


async def test_create_topic_rule_requires_embeddings(client, users):
    user = await users.create()
    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "topic", "phrase": "anything"},
        headers=users.auth(user),
    )
    assert resp.status_code == 422


async def test_create_topic_rule_embed_failure(client, users, monkeypatch):
    user = await users.create()
    _configure_embeddings(monkeypatch)

    async def boom(texts):
        raise RuntimeError("embed down")

    monkeypatch.setattr(interests.embeddings, "embed_texts", boom)
    resp = await client.post(
        "/api/interests/dislikes",
        json={"kind": "topic", "phrase": "anything"},
        headers=users.auth(user),
    )
    assert resp.status_code == 502


async def test_create_validation_mismatches(client, users):
    user = await users.create()
    for body in (
        {"kind": "article"},
        {"kind": "story"},
        {"kind": "entity"},
        {"kind": "topic"},
        {"kind": "topic", "phrase": "   "},
        {"kind": "nonsense", "article_id": 1},
    ):
        resp = await client.post("/api/interests/dislikes", json=body, headers=users.auth(user))
        assert resp.status_code == 422, body


# --- GET /interests/dislikes + /{id}/articles, DELETE ---


async def test_list_rules_scoped_with_counts(client, users, data, session):
    user = await users.create()
    other = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    await data.subscribe(other, feed)
    art = await data.article(feed)
    await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )

    mine = await client.get("/api/interests/dislikes", headers=users.auth(user))
    assert [r["hidden_count"] for r in mine.json()] == [1]
    theirs = await client.get("/api/interests/dislikes", headers=users.auth(other))
    assert theirs.json() == []


async def test_rule_articles_and_ownership(client, users, data):
    user = await users.create()
    other = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed, title="Hidden one")
    created = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    rule_id = created.json()["rule"]["id"]

    resp = await client.get(f"/api/interests/dislikes/{rule_id}/articles", headers=users.auth(user))
    assert resp.json() == [{"id": art.id, "title": "Hidden one"}]
    denied = await client.get(
        f"/api/interests/dislikes/{rule_id}/articles", headers=users.auth(other)
    )
    assert denied.status_code == 404


async def test_delete_rule_unhides_articles(client, users, data, session):
    user = await users.create()
    other = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    created = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    rule_id = created.json()["rule"]["id"]

    denied = await client.delete(f"/api/interests/dislikes/{rule_id}", headers=users.auth(other))
    assert denied.status_code == 404

    resp = await client.delete(f"/api/interests/dislikes/{rule_id}", headers=users.auth(user))
    assert resp.status_code == 204
    assert (await session.scalar(select(ArticleSuppression))) is None  # cascade
    listing = await client.get("/api/articles", headers=users.auth(user))
    assert art.id in [a["id"] for a in listing.json()]


async def test_options_crypto_error_falls_back_to_system(client, users, data, monkeypatch):
    from app import crypto

    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)
    _configure_embeddings(monkeypatch)

    async def broken_resolve(session_, user_id):
        raise crypto.TokenCryptoError("key rotated")

    monkeypatch.setattr(interests.llm, "resolve_config", broken_resolve)
    monkeypatch.setattr(interests.llm, "system_config", lambda: None)  # no server key either

    resp = await client.get(f"/api/interests/dislike-options/{art.id}", headers=users.auth(user))
    assert resp.status_code == 200
    assert resp.json()["topics"] == []


async def test_create_duplicate_race_returns_winner(client, users, data, monkeypatch):
    """Two concurrent creates for the same reason: the pre-check misses the
    in-flight twin, the unique partial index rejects the loser, and the
    endpoint returns the winner's rule instead of a 500."""
    user = await users.create()
    feed = await data.feed()
    await data.subscribe(user, feed)
    art = await data.article(feed)

    real_check = interests._existing_rule
    calls = {"n": 0}

    async def racy_check(session, user_id, body):
        calls["n"] += 1
        if calls["n"] <= 2:  # both requests pass the pre-check
            return None
        return await real_check(session, user_id, body)

    monkeypatch.setattr(interests, "_existing_rule", racy_check)

    first = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    second = await client.post(
        "/api/interests/dislikes",
        json={"kind": "article", "article_id": art.id},
        headers=users.auth(user),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["rule"]["id"] == first.json()["rule"]["id"]
