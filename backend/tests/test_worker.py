import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import worker
from app.models import Article, ArticleEmbedding, Feed
from app.summarizer import ThinContentError


async def _feed(session, **kwargs):
    feed = Feed(url=f"https://feed/{kwargs.get('url', 'x')}",
                last_fetched_at=kwargs.get("last_fetched_at"))
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def _article(session, feed, **kwargs):
    defaults = dict(guid=f"g{id(kwargs)}", url="https://x/a", title="T",
                    content_html="", excerpt="", full_text="", image_url=None)
    defaults.update(kwargs)
    art = Article(feed_id=feed.id, **defaults)
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


# --- _enrich_one / _summarize_one ---

async def test_enrich_one_missing_article(monkeypatch):
    sem = asyncio.Semaphore(1)
    # No article with this id -> returns without calling enrich_article.
    await worker._enrich_one(99999, sem)


async def test_enrich_one_calls_enricher(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)
    called = {}

    async def fake_enrich(s, article):
        called["id"] = article.id

    monkeypatch.setattr(worker, "enrich_article", fake_enrich)
    await worker._enrich_one(art.id, asyncio.Semaphore(1))
    assert called["id"] == art.id


async def test_enrich_one_swallows_errors(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def boom(s, article):
        raise RuntimeError("enrich failed")

    monkeypatch.setattr(worker, "enrich_article", boom)
    await worker._enrich_one(art.id, asyncio.Semaphore(1))  # no raise


async def test_summarize_one_thin_content(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def raise_thin(s, article, allow_refetch=False):
        raise ThinContentError()

    monkeypatch.setattr(worker, "generate_summaries", raise_thin)
    await worker._summarize_one(art.id, asyncio.Semaphore(1))  # no raise


async def test_summarize_one_generic_error(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def boom(s, article, allow_refetch=False):
        raise RuntimeError("oops")

    monkeypatch.setattr(worker, "generate_summaries", boom)
    await worker._summarize_one(art.id, asyncio.Semaphore(1))


async def test_summarize_one_missing(monkeypatch):
    await worker._summarize_one(99999, asyncio.Semaphore(1))


# --- enrich_and_summarize orchestration ---

async def test_enrich_and_summarize_no_llm(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed, full_text="", image_url=None)

    enriched = []

    async def fake_enrich(s, article):
        enriched.append(article.id)

    async def fake_extract(feed_id=None):
        return 2

    monkeypatch.setattr(worker, "enrich_article", fake_enrich)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)

    await worker.enrich_and_summarize()
    assert len(enriched) == 1


async def test_enrich_and_summarize_extract_failure(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed)

    async def fake_enrich(s, article):
        pass

    async def boom(feed_id=None):
        raise RuntimeError("extract down")

    monkeypatch.setattr(worker, "enrich_article", fake_enrich)
    monkeypatch.setattr(worker, "extract_entities", boom)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)
    await worker.enrich_and_summarize()  # extract error swallowed


async def test_enrich_and_summarize_full_pipeline(session, monkeypatch):
    feed = await _feed(session)
    # Article needing enrich + summary.
    art = await _article(session, feed, full_text="", image_url=None, summary_short="")

    async def fake_enrich(s, article):
        article.full_text = "text"

    async def fake_extract(feed_id=None):
        return 0

    summarized = []

    async def fake_summarize(s, article, allow_refetch=False):
        summarized.append(article.id)
        article.summary_short = "s"

    async def fake_embed(feed_id=None):
        return 3

    monkeypatch.setattr(worker, "enrich_article", fake_enrich)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker, "generate_summaries", fake_summarize)
    monkeypatch.setattr(worker, "embed_articles_batch", fake_embed)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)

    await worker.enrich_and_summarize(feed_id=feed.id)
    assert summarized == [art.id]


async def test_enrich_and_summarize_scoped_to_feed(session, monkeypatch):
    feed1 = await _feed(session, url="one")
    feed2 = await _feed(session, url="two")
    await _article(session, feed1, guid="f1")
    await _article(session, feed2, guid="f2")

    enriched = []

    async def fake_enrich(s, article):
        enriched.append(article.feed_id)

    async def fake_extract(feed_id=None):
        return 0

    monkeypatch.setattr(worker, "enrich_article", fake_enrich)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)

    await worker.enrich_and_summarize(feed_id=feed1.id)
    assert enriched == [feed1.id]


async def test_enrich_and_summarize_converges_when_nothing_fetchable(session, monkeypatch):
    # Regression: an article with a rich feed body and an image already set
    # still matches the enrich batch query while full_text == '' and the stamp
    # is NULL. The real enrich_article must stamp it (without fetching), or the
    # worker re-selects it every cycle and pending_count never reaches zero.
    from app import extractor

    feed = await _feed(session, url="converge")
    rich = "<p>" + ("word " * 200) + "</p>"
    art = await _article(session, feed, content_html=rich, image_url="https://x/i.png")

    async def no_fetch(url):
        raise AssertionError("nothing to fetch for this article")

    async def fake_extract(feed_id=None):
        return 0

    monkeypatch.setattr(extractor, "fetch_page", no_fetch)
    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)

    await worker.enrich_and_summarize(feed_id=feed.id)

    await session.refresh(art)
    assert art.full_text_fetched_at is not None


async def test_enrich_and_summarize_skips_ai_disabled_feed(session, monkeypatch):
    feed = await _feed(session, url="noai")
    feed.ai_enabled = False
    await session.commit()
    # Already enriched so only the summarize stage would pick it up.
    art = await _article(session, feed, full_text="text", summary_short="",
                         full_text_fetched_at=datetime.now(timezone.utc),
                         image_url="https://x/i.png")

    async def fake_extract(feed_id=None):
        return 0

    summarized = []

    async def fake_summarize(s, article, allow_refetch=False):
        summarized.append(article.id)

    async def fake_embed(feed_id=None):
        return 0

    monkeypatch.setattr(worker, "extract_entities", fake_extract)
    monkeypatch.setattr(worker, "generate_summaries", fake_summarize)
    monkeypatch.setattr(worker, "embed_articles_batch", fake_embed)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)

    await worker.enrich_and_summarize(feed_id=feed.id)
    assert summarized == []


async def test_embed_articles_batch_skips_ai_disabled_feed(session, monkeypatch):
    feed = await _feed(session, url="noai-embed")
    feed.ai_enabled = False
    await session.commit()
    await _article(session, feed, excerpt="body")
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: True)

    captured = {}

    async def fake_embed(s, articles):
        captured["n"] = len(articles)
        return len(articles)

    monkeypatch.setattr(worker.embeddings, "embed_articles", fake_embed)
    await worker.embed_articles_batch()
    assert captured["n"] == 0


# --- embed_articles_batch ---

async def test_embed_articles_batch_not_configured(monkeypatch):
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: False)
    assert await worker.embed_articles_batch() == 0


async def test_embed_articles_batch_writes(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed, excerpt="body")
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: True)

    async def fake_embed(s, articles):
        return len(articles)

    monkeypatch.setattr(worker.embeddings, "embed_articles", fake_embed)
    assert await worker.embed_articles_batch() == 1


async def test_embed_articles_batch_error(session, monkeypatch):
    feed = await _feed(session)
    await _article(session, feed, excerpt="body")
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: True)

    async def boom(s, articles):
        raise RuntimeError("embed down")

    monkeypatch.setattr(worker.embeddings, "embed_articles", boom)
    assert await worker.embed_articles_batch() == 0


async def test_embed_articles_batch_scoped_and_skips_current_model(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, excerpt="body")
    session.add(ArticleEmbedding(article_id=art.id, model="current", embedding=[0.1, 0.2]))
    await session.commit()
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(worker.settings, "openai_embedding_model", "current")

    captured = {}

    async def fake_embed(s, articles):
        captured["n"] = len(articles)
        return len(articles)

    monkeypatch.setattr(worker.embeddings, "embed_articles", fake_embed)
    await worker.embed_articles_batch(feed_id=feed.id)
    # Article already embedded with the current model -> nothing to embed.
    assert captured["n"] == 0


# --- enrich_feed / refresh_entities ---

async def test_enrich_feed(monkeypatch):
    called = {}

    async def fake(ctx, feed_id=None):
        called["feed_id"] = feed_id

    monkeypatch.setattr(worker, "enrich_and_summarize", fake)
    await worker.enrich_feed({}, 42)
    assert called["feed_id"] == 42


async def test_refresh_entities(monkeypatch):
    called = {}

    async def fake():
        called["ran"] = True
        return 5

    monkeypatch.setattr(worker, "refresh_stale_entities", fake)
    await worker.refresh_entities({})
    assert called["ran"]


async def test_refresh_entities_error(monkeypatch):
    async def boom():
        raise RuntimeError("refresh down")

    monkeypatch.setattr(worker, "refresh_stale_entities", boom)
    await worker.refresh_entities({})  # swallowed


# --- poll_feeds ---

async def test_poll_feeds_refreshes_due(session, monkeypatch):
    # Never-fetched feed is due.
    await _feed(session, url="due")
    # Recently fetched feed is not due.
    await _feed(session, url="fresh",
                last_fetched_at=datetime.now(timezone.utc))

    refreshed = []

    async def fake_refresh(s, feed):
        refreshed.append(feed.url)

    async def fake_enrich(ctx):
        pass

    monkeypatch.setattr(worker, "refresh_feed", fake_refresh)
    monkeypatch.setattr(worker, "enrich_and_summarize", fake_enrich)
    await worker.poll_feeds({})
    assert any("due" in u for u in refreshed)
    assert not any("fresh" in u for u in refreshed)


async def test_poll_feeds_refresh_error_rolls_back(session, monkeypatch):
    await _feed(session, url="due")

    async def boom(s, feed):
        raise RuntimeError("network")

    async def fake_enrich(ctx):
        pass

    monkeypatch.setattr(worker, "refresh_feed", boom)
    monkeypatch.setattr(worker, "enrich_and_summarize", fake_enrich)
    await worker.poll_feeds({})  # error swallowed, still runs enrich


async def test_poll_feeds_due_by_interval(session, monkeypatch):
    # Fetched long ago relative to its interval -> due.
    feed = Feed(url="https://feed/old", refresh_interval_minutes=15,
                last_fetched_at=datetime.now(timezone.utc) - timedelta(hours=1))
    session.add(feed)
    await session.commit()

    refreshed = []

    async def fake_refresh(s, f):
        refreshed.append(f.id)

    async def fake_enrich(ctx):
        pass

    monkeypatch.setattr(worker, "refresh_feed", fake_refresh)
    monkeypatch.setattr(worker, "enrich_and_summarize", fake_enrich)
    await worker.poll_feeds({})
    assert refreshed == [feed.id]


# --- startup ---

async def test_startup(monkeypatch):
    called = {}

    async def fake_init():
        called["init"] = True

    monkeypatch.setattr(worker, "init_db", fake_init)
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)
    await worker.startup({})
    assert called["init"]


def test_worker_settings_shape():
    assert worker.WorkerSettings.functions == [worker.enrich_feed]
    assert len(worker.WorkerSettings.cron_jobs) == 2
