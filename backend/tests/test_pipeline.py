import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.enrichers import pipeline
from app.enrichers.base import EnrichError
from app.models import Article, ArticleEntity, Entity, EntitySnapshot, Feed


async def _feed(session):
    feed = Feed(url="https://feed/x")
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def _article(session, feed, **kwargs):
    defaults = dict(guid="g1", url="https://github.com/pytorch/pytorch", title="T",
                    content_html="", comments_url=None,
                    published_at=datetime.now(timezone.utc))
    defaults.update(kwargs)
    art = Article(feed_id=feed.id, **defaults)
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


def _patch_client(monkeypatch):
    """Neutralise the outbound httpx client the pipeline builds."""
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(pipeline, "_make_client", lambda: FakeClient())


# --- _get_or_refresh ---

async def test_get_or_refresh_fetches_and_snapshots(session, monkeypatch):
    from app.enrichers.github import GitHubEnricher

    enricher = GitHubEnricher()

    async def fake_fetch(key, client):
        return {"full_name": key, "stargazers_count": 10}

    monkeypatch.setattr(enricher, "fetch", fake_fetch)
    entity = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    assert entity.canonical_key == "a/b"
    assert entity.data["stargazers_count"] == 10
    snapshots = (await session.scalars(select(EntitySnapshot))).all()
    assert len(snapshots) == 1


async def test_get_or_refresh_uses_fresh_cache(session, monkeypatch):
    from app.enrichers.github import GitHubEnricher

    enricher = GitHubEnricher()
    entity = Entity(kind="github", canonical_key="a/b", url="u",
                    data={"stargazers_count": 5},
                    fetched_at=datetime.now(timezone.utc))
    session.add(entity)
    await session.commit()

    async def fail_fetch(key, client):
        raise AssertionError("should not fetch fresh entity")

    monkeypatch.setattr(enricher, "fetch", fail_fetch)
    result = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    assert result.data["stargazers_count"] == 5


async def test_get_or_refresh_enrich_error_returns_stale(session, monkeypatch):
    from app.enrichers.github import GitHubEnricher

    enricher = GitHubEnricher()
    entity = Entity(kind="github", canonical_key="a/b", url="u",
                    data={"stargazers_count": 5},
                    fetched_at=datetime.now(timezone.utc) - timedelta(days=1))
    session.add(entity)
    await session.commit()

    async def raise_enrich(key, client):
        raise EnrichError("404")

    monkeypatch.setattr(enricher, "fetch", raise_enrich)
    result = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    assert result.data["stargazers_count"] == 5  # stale row returned


async def test_get_or_refresh_generic_error_returns_stale(session, monkeypatch):
    from app.enrichers.github import GitHubEnricher

    enricher = GitHubEnricher()

    async def boom(key, client):
        raise RuntimeError("network")

    monkeypatch.setattr(enricher, "fetch", boom)
    # No existing entity -> returns None.
    result = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    assert result is None


async def test_get_or_refresh_snapshots_only_on_change(session, monkeypatch):
    from app.enrichers.github import GitHubEnricher

    enricher = GitHubEnricher()
    payloads = [{"stargazers_count": 1}, {"stargazers_count": 1}, {"stargazers_count": 2}]

    async def fetch(key, client):
        return payloads.pop(0)

    monkeypatch.setattr(enricher, "fetch", fetch)
    # First fetch: creates + snapshot.
    e = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    e.fetched_at = datetime.now(timezone.utc) - timedelta(days=1)
    await session.commit()
    # Second fetch: identical data -> no new snapshot.
    e = await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    e.fetched_at = datetime.now(timezone.utc) - timedelta(days=1)
    await session.commit()
    # Third fetch: changed data -> new snapshot.
    await pipeline._get_or_refresh(session, enricher, "a/b", client=None)
    snapshots = (await session.scalars(select(EntitySnapshot))).all()
    assert len(snapshots) == 2


# --- link_article_entities ---

async def test_link_article_entities(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(
        session, feed,
        url="https://github.com/pytorch/pytorch",
        content_html='<a href="https://arxiv.org/abs/1706.03762">paper</a>',
    )

    async def fake_get_or_refresh(s, enricher, key, client):
        entity = Entity(kind=enricher.kind, canonical_key=key,
                        url=enricher.entity_url(key), data={"x": 1},
                        fetched_at=datetime.now(timezone.utc))
        s.add(entity)
        await s.flush()
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_get_or_refresh)
    locks = defaultdict(asyncio.Lock)
    count = await pipeline.link_article_entities(session, art, None, locks)
    assert count == 2  # primary github + inline arxiv
    links = (await session.scalars(select(ArticleEntity))).all()
    assert {l.source for l in links} == {"primary", "inline"}


async def test_link_article_entities_dedupes(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(
        session, feed,
        url="https://github.com/a/b",
        content_html='<a href="https://github.com/a/b">same</a>',
    )

    async def fake_get_or_refresh(s, enricher, key, client):
        entity = Entity(kind=enricher.kind, canonical_key=key,
                        url=enricher.entity_url(key), data={"x": 1},
                        fetched_at=datetime.now(timezone.utc))
        s.add(entity)
        await s.flush()
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_get_or_refresh)
    count = await pipeline.link_article_entities(session, art, None, defaultdict(asyncio.Lock))
    assert count == 1  # same repo from url + inline deduped


async def test_link_article_entities_scans_full_text(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(
        session, feed,
        url="https://example.com/story",
        content_html="",
        full_text="Great write-up. The code lives at https://github.com/a/b, worth a star.",
    )

    async def fake_refresh(s, enricher, key, client):
        entity = Entity(kind=enricher.kind, canonical_key=key,
                        url=enricher.entity_url(key), data={},
                        fetched_at=datetime.now(timezone.utc))
        s.add(entity)
        await s.flush()
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_refresh)
    count = await pipeline.link_article_entities(session, art, None, defaultdict(asyncio.Lock))
    assert count == 1
    link = await session.scalar(select(ArticleEntity))
    assert link.source == "inline"


async def test_link_article_entities_skips_unresolvable(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, url="https://github.com/a/b")

    async def none_refresh(s, enricher, key, client):
        return None

    monkeypatch.setattr(pipeline, "_get_or_refresh", none_refresh)
    count = await pipeline.link_article_entities(session, art, None, defaultdict(asyncio.Lock))
    assert count == 0


async def test_link_article_entities_with_comments_url(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, url="https://example.com/story",
                         comments_url="https://github.com/a/b")

    async def fake_refresh(s, enricher, key, client):
        entity = Entity(kind=enricher.kind, canonical_key=key,
                        url=enricher.entity_url(key), data={},
                        fetched_at=datetime.now(timezone.utc))
        s.add(entity)
        await s.flush()
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_refresh)
    count = await pipeline.link_article_entities(session, art, None, defaultdict(asyncio.Lock))
    assert count == 1


async def test_link_article_entities_caps_per_article(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(
        session, feed,
        url="https://github.com/a/b",
        content_html='<a href="https://pypi.org/project/requests/">pkg</a>',
    )
    monkeypatch.setattr(pipeline, "MAX_ENTITIES_PER_ARTICLE", 1)

    async def fake_refresh(s, enricher, key, client):
        entity = Entity(kind=enricher.kind, canonical_key=key,
                        url=enricher.entity_url(key), data={},
                        fetched_at=datetime.now(timezone.utc))
        s.add(entity)
        await s.flush()
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_refresh)
    count = await pipeline.link_article_entities(session, art, None, defaultdict(asyncio.Lock))
    assert count == 1  # capped even though two entities are present


def test_make_client_returns_async_client():
    client = pipeline._make_client()
    assert isinstance(client, httpx.AsyncClient)


async def test_extract_one_missing_article(session, monkeypatch):
    class FakeClient:
        pass

    # Article id that doesn't exist -> early return, no link call.
    await pipeline._extract_one(99999, asyncio.Semaphore(1), FakeClient(), defaultdict(asyncio.Lock))


# --- extract_entities ---

async def test_extract_entities_none_pending(session, monkeypatch):
    _patch_client(monkeypatch)
    assert await pipeline.extract_entities() == 0


async def test_extract_entities_processes(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed, url="https://github.com/a/b")
    _patch_client(monkeypatch)

    async def fake_link(s, article, client, locks):
        return 1

    monkeypatch.setattr(pipeline, "link_article_entities", fake_link)
    count = await pipeline.extract_entities()
    assert count == 1
    art = await session.scalar(select(Article))
    await session.refresh(art)
    assert art.entities_extracted_at is not None


async def test_extract_entities_rescans_after_late_fulltext(session, monkeypatch):
    """An article scanned before its full text arrived is scanned again —
    its body links were invisible the first time — and the fresh stamp
    stops the loop."""
    feed = await _feed(session)
    now = datetime.now(timezone.utc)
    art = await _article(
        session, feed,
        entities_extracted_at=now - timedelta(hours=2),
        full_text_fetched_at=now - timedelta(hours=1),
    )
    _patch_client(monkeypatch)

    seen = []

    async def fake_link(s, article, client, locks):
        seen.append(article.id)
        return 0

    monkeypatch.setattr(pipeline, "link_article_entities", fake_link)
    assert await pipeline.extract_entities() == 1
    assert seen == [art.id]
    # Rescan stamped now() > full_text_fetched_at -> converged.
    assert await pipeline.extract_entities() == 0


async def test_extract_entities_stamps_even_on_error(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed)
    _patch_client(monkeypatch)

    async def boom(s, article, client, locks):
        raise RuntimeError("link failed")

    monkeypatch.setattr(pipeline, "link_article_entities", boom)
    await pipeline.extract_entities()
    art = await session.scalar(select(Article))
    await session.refresh(art)
    assert art.entities_extracted_at is not None  # stamped despite failure


async def test_extract_entities_scoped_to_feed(session, monkeypatch):
    feed1 = await _feed(session)
    feed2 = Feed(url="https://feed/2")
    session.add(feed2)
    await session.commit()
    await session.refresh(feed2)
    await _article(session, feed1, guid="a1")
    await _article(session, feed2, guid="a2")
    _patch_client(monkeypatch)

    seen = []

    async def fake_link(s, article, client, locks):
        seen.append(article.feed_id)
        return 0

    monkeypatch.setattr(pipeline, "link_article_entities", fake_link)
    await pipeline.extract_entities(feed_id=feed1.id)
    assert seen == [feed1.id]


# --- refresh_stale_entities ---

async def test_refresh_stale_entities_none(session, monkeypatch):
    _patch_client(monkeypatch)
    assert await pipeline.refresh_stale_entities() == 0


async def test_refresh_stale_entities_refreshes(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, published_at=datetime.now(timezone.utc))
    # Stale github entity referenced by the fresh article.
    entity = Entity(kind="github", canonical_key="a/b", url="u", data={"stargazers_count": 1},
                    fetched_at=datetime.now(timezone.utc) - timedelta(days=30))
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    await session.commit()

    _patch_client(monkeypatch)
    refreshed = []

    async def fake_refresh(s, enricher, key, client):
        refreshed.append(key)
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_refresh)
    count = await pipeline.refresh_stale_entities()
    assert count == 1
    assert refreshed == ["a/b"]


async def test_refresh_stale_entities_hits_batch_cap(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, published_at=datetime.now(timezone.utc))
    entity = Entity(kind="github", canonical_key="a/b", url="u", data={"x": 1},
                    fetched_at=datetime.now(timezone.utc) - timedelta(days=30))
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    await session.commit()

    monkeypatch.setattr(pipeline, "REFRESH_BATCH", 1)
    _patch_client(monkeypatch)

    async def fake_refresh(s, enricher, key, client):
        return entity

    monkeypatch.setattr(pipeline, "_get_or_refresh", fake_refresh)
    # With REFRESH_BATCH=1, the loop breaks after the first enricher fills the batch.
    count = await pipeline.refresh_stale_entities()
    assert count == 1


async def test_refresh_stale_entities_swallows_refresh_error(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, published_at=datetime.now(timezone.utc))
    entity = Entity(kind="github", canonical_key="a/b", url="u", data={"x": 1},
                    fetched_at=datetime.now(timezone.utc) - timedelta(days=30))
    session.add(entity)
    await session.flush()
    session.add(ArticleEntity(article_id=art.id, entity_id=entity.id, source="primary", position=0))
    await session.commit()

    _patch_client(monkeypatch)

    async def boom(s, enricher, key, client):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(pipeline, "_get_or_refresh", boom)
    count = await pipeline.refresh_stale_entities()
    assert count == 1  # counted as attempted; error swallowed
