from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import db as app_db
from app import suppressions, worker
from app.models import (
    Article,
    ArticleEmbedding,
    ArticleEntity,
    ArticleSuppression,
    DislikeRuleEmbedding,
    Entity,
    Feed,
    Subscription,
    UserDislikeRule,
)

NOW = datetime.now(UTC)


async def _feed(session, url="sup"):
    feed = Feed(url=f"https://feed/{url}")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def _article(session, feed, *, guid, fetched_at=None, **kwargs):
    art = Article(feed_id=feed.id, guid=guid, url="https://x/a", title=f"T {guid}", **kwargs)
    session.add(art)
    await session.commit()
    if fetched_at is not None:
        art.fetched_at = fetched_at
        await session.commit()
    await session.refresh(art)
    return art


async def _subscribe(session, user, feed):
    session.add(Subscription(user_id=user.id, feed_id=feed.id))
    await session.commit()


async def _entity_rule(session, user, entity):
    rule = UserDislikeRule(
        user_id=user.id, kind="entity", entity_id=entity.id, label=entity.canonical_key
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


async def _vector_rule(
    session, user, vector, *, kind="topic", threshold=0.5, expires_at=None, model="test-model"
):
    rule = UserDislikeRule(
        user_id=user.id, kind=kind, threshold=threshold, expires_at=expires_at, label="rule"
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    session.add(DislikeRuleEmbedding(rule_id=rule.id, model=model, embedding=vector))
    await session.commit()
    return rule


async def _suppressed_ids(session, user):
    return set(
        await session.scalars(
            select(ArticleSuppression.article_id).where(ArticleSuppression.user_id == user.id)
        )
    )


# --- entity leg ---


async def test_entity_rule_suppresses_for_subscriber_only(session, users):
    subscriber = await users.create(username="sub")
    outsider = await users.create(username="out")
    feed = await _feed(session)
    await _subscribe(session, subscriber, feed)
    art = await _article(session, feed, guid="linked")
    entity = Entity(kind="github", canonical_key="acme/widget", url="https://gh/acme/widget")
    session.add(entity)
    await session.commit()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary"))
    await session.commit()
    await _entity_rule(session, subscriber, entity)
    await _entity_rule(session, outsider, entity)  # not subscribed to the feed

    cutoff = NOW - suppressions.SUPPRESS_WINDOW
    assert await suppressions.apply_entity_rules(session, cutoff=cutoff) == 1
    await session.commit()
    assert await _suppressed_ids(session, subscriber) == {art.id}
    assert await _suppressed_ids(session, outsider) == set()


async def test_entity_rule_idempotent_and_window_scoped(session, users):
    user = await users.create(username="u")
    feed = await _feed(session)
    await _subscribe(session, user, feed)
    entity = Entity(kind="pypi", canonical_key="leftpad", url="https://pypi/leftpad")
    session.add(entity)
    await session.commit()
    fresh = await _article(session, feed, guid="fresh")
    stale = await _article(session, feed, guid="stale", fetched_at=NOW - timedelta(days=40))
    for art in (fresh, stale):
        session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary"))
    await session.commit()
    rule = await _entity_rule(session, user, entity)

    cutoff = NOW - suppressions.BACKFILL_WINDOW
    assert await suppressions.apply_entity_rules(session, cutoff=cutoff, rule_id=rule.id) == 1
    await session.commit()
    assert await _suppressed_ids(session, user) == {fresh.id}
    # Second run inserts nothing (ON CONFLICT DO NOTHING).
    assert await suppressions.apply_entity_rules(session, cutoff=cutoff, rule_id=rule.id) == 0


# --- vector leg ---


async def test_vector_rule_threshold_and_model_match(session, users):
    user = await users.create(username="v")
    feed = await _feed(session)
    await _subscribe(session, user, feed)
    near = await _article(session, feed, guid="near")
    far = await _article(session, feed, guid="far")
    other_model = await _article(session, feed, guid="othermodel")
    unembedded = await _article(session, feed, guid="bare")
    session.add(ArticleEmbedding(article_id=near.id, model="test-model", embedding=[1.0, 0.0, 0.0]))
    session.add(ArticleEmbedding(article_id=far.id, model="test-model", embedding=[0.0, 1.0, 0.0]))
    session.add(
        ArticleEmbedding(article_id=other_model.id, model="legacy", embedding=[1.0, 0.0, 0.0])
    )
    await session.commit()
    await _vector_rule(session, user, [1.0, 0.0, 0.0], threshold=0.5)

    cutoff = NOW - suppressions.SUPPRESS_WINDOW
    assert await suppressions.apply_vector_rules(session, cutoff=cutoff) == 1
    await session.commit()
    # Only the same-model, below-threshold article; unembedded stays visible (fail-open).
    assert await _suppressed_ids(session, user) == {near.id}
    assert unembedded.id not in await _suppressed_ids(session, user)


async def test_vector_rule_expired_not_applied(session, users):
    user = await users.create(username="exp")
    feed = await _feed(session)
    await _subscribe(session, user, feed)
    art = await _article(session, feed, guid="a")
    session.add(ArticleEmbedding(article_id=art.id, model="test-model", embedding=[1.0, 0.0, 0.0]))
    await session.commit()
    await _vector_rule(
        session, user, [1.0, 0.0, 0.0], kind="story", expires_at=NOW - timedelta(days=1)
    )

    cutoff = NOW - suppressions.SUPPRESS_WINDOW
    assert await suppressions.apply_vector_rules(session, cutoff=cutoff) == 0


async def test_vector_rules_noop_without_pgvector(session, monkeypatch):
    monkeypatch.setattr(app_db, "vector_enabled", False)
    assert await suppressions.apply_vector_rules(session, cutoff=NOW) == 0


# --- worker stage ---


async def test_suppress_articles_batch_deletes_expired_rules(session, users):
    user = await users.create(username="wk")
    feed = await _feed(session)
    await _subscribe(session, user, feed)
    art = await _article(session, feed, guid="a")
    session.add(ArticleEmbedding(article_id=art.id, model="test-model", embedding=[1.0, 0.0, 0.0]))
    await session.commit()
    expired = await _vector_rule(
        session, user, [1.0, 0.0, 0.0], kind="story", expires_at=NOW - timedelta(days=1)
    )
    session.add(ArticleSuppression(user_id=user.id, article_id=art.id, rule_id=expired.id))
    live = await _vector_rule(
        session, user, [1.0, 0.0, 0.0], kind="story", expires_at=NOW + timedelta(days=13)
    )
    await session.commit()

    assert await worker.suppress_articles_batch(feed_id=feed.id) == 1

    rules = set(await session.scalars(select(UserDislikeRule.id)))
    assert rules == {live.id}
    # The expired rule's suppression cascaded away; the live rule re-suppressed.
    remaining = set(
        await session.scalars(
            select(ArticleSuppression.rule_id).where(ArticleSuppression.user_id == user.id)
        )
    )
    assert remaining == {live.id}


async def test_suppress_articles_batch_swallows_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(worker.suppressions, "apply_entity_rules", boom)
    assert await worker.suppress_articles_batch() == 0


async def test_enrich_and_summarize_runs_suppression_without_llm(session, monkeypatch):
    called = []

    async def fake_suppress(feed_id=None):
        called.append(feed_id)
        return 0

    async def fake_extract(feed_id=None):
        return 0

    monkeypatch.setattr(worker, "suppress_articles_batch", fake_suppress)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)
    await worker.enrich_and_summarize(feed_id=7)
    assert called == [7]


async def test_enrich_and_summarize_runs_suppression_after_embed(session, monkeypatch):
    order = []

    async def fake_suppress(feed_id=None):
        order.append("suppress")
        return 1

    async def fake_embed(feed_id=None):
        order.append("embed")
        return 0

    async def fake_extract(feed_id=None):
        return 0

    monkeypatch.setattr(worker, "suppress_articles_batch", fake_suppress)
    monkeypatch.setattr(worker, "embed_articles_batch", fake_embed)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)
    await worker.enrich_and_summarize()
    assert order == ["embed", "suppress"]
