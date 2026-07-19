from datetime import UTC, datetime, timedelta

from app import worker
from app.models import Article, ArticleEmbedding, Feed
from app.summarizer import SummarySkipped, ThinContentError


async def _feed(session, **kwargs):
    feed = Feed(
        url=f"https://feed/{kwargs.get('url', 'x')}", last_fetched_at=kwargs.get("last_fetched_at")
    )
    session.add(feed)
    await session.commit()
    await session.refresh(feed)
    return feed


async def _article(session, feed, **kwargs):
    defaults = dict(
        guid=f"g{id(kwargs)}",
        url="https://x/a",
        title="T",
        content_html="",
        excerpt="",
        full_text="",
        image_url=None,
    )
    defaults.update(kwargs)
    art = Article(feed_id=feed.id, **defaults)
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


# --- _for_each_article / _summarize_quietly ---


async def test_for_each_article_missing_article():
    async def fail(s, article):
        raise AssertionError("fn must not be called for a missing article")

    await worker._for_each_article([99999], concurrency=1, label="Enrichment", fn=fail)


async def test_for_each_article_calls_fn(session):
    feed = await _feed(session)
    art = await _article(session, feed)
    called = {}

    async def fake_enrich(s, article):
        called["id"] = article.id

    await worker._for_each_article([art.id], concurrency=1, label="Enrichment", fn=fake_enrich)
    assert called["id"] == art.id


async def test_for_each_article_swallows_errors(session):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def boom(s, article):
        raise RuntimeError("enrich failed")

    await worker._for_each_article([art.id], concurrency=1, label="Enrichment", fn=boom)  # no raise


async def test_summarize_quietly_thin_content(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def raise_thin(s, article, allow_refetch=False):
        raise ThinContentError()

    monkeypatch.setattr(worker, "generate_summaries", raise_thin)
    await worker._for_each_article(
        [art.id], concurrency=1, label="Auto-summary", fn=worker._summarize_quietly
    )  # no raise


async def test_summarize_quietly_short_content(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def skip(s, article, allow_refetch=False):
        raise SummarySkipped()

    monkeypatch.setattr(worker, "generate_summaries", skip)
    await worker._for_each_article(
        [art.id], concurrency=1, label="Auto-summary", fn=worker._summarize_quietly
    )  # no raise


async def test_summarize_quietly_generic_error(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed)

    async def boom(s, article, allow_refetch=False):
        raise RuntimeError("oops")

    monkeypatch.setattr(worker, "generate_summaries", boom)
    await worker._for_each_article(
        [art.id], concurrency=1, label="Auto-summary", fn=worker._summarize_quietly
    )  # swallowed and logged by the batch helper


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
    skipped = await _article(
        session,
        feed,
        guid="already-short",
        full_text="short",
        image_url="https://x/short.png",
        summary_skipped_reason="too_short",
    )

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
    assert skipped.id not in summarized


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
    await _article(
        session,
        feed,
        full_text="text",
        summary_short="",
        full_text_fetched_at=datetime.now(UTC),
        image_url="https://x/i.png",
    )

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
    session.add(
        ArticleEmbedding(
            article_id=art.id,
            model="current",
            embedding=[0.1, 0.2],
            input_hash=worker.embeddings.input_hash_for(art),
        )
    )
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


async def test_ner_batch_not_configured(monkeypatch):
    monkeypatch.setattr(worker.llm, "is_configured", lambda: False)
    assert await worker.extract_named_entities_batch() == 0


async def test_ner_batch_selects_stamps_and_retags(session, monkeypatch):
    feed = await _feed(session)
    now = datetime.now(UTC)
    ready = await _article(
        session,
        feed,
        guid="ready",
        full_text_fetched_at=now,
        full_text="body",
        summary_skipped_reason="too_short",
    )
    await _article(session, feed, guid="pending")  # never enriched: wait
    # Tagged before its summary existed -> re-tagged.
    stale = await _article(
        session,
        feed,
        guid="stale",
        summary_medium="sum",
        summary_generated_at=now,
        ner_extracted_at=now - timedelta(hours=1),
    )
    await _article(
        session,
        feed,
        guid="done",
        summary_medium="sum",
        summary_generated_at=now - timedelta(hours=1),
        ner_extracted_at=now,
    )
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)

    seen = []

    async def fake_extract(s, article, **kwargs):
        seen.append(article.id)
        return 1

    monkeypatch.setattr(worker.ner, "extract_named", fake_extract)
    assert await worker.extract_named_entities_batch() == 2
    assert set(seen) == {ready.id, stale.id}
    await session.refresh(ready)
    assert ready.ner_extracted_at is not None
    # Second run: everything stamped and converged.
    assert await worker.extract_named_entities_batch() == 0


async def test_ner_batch_stamps_even_on_error(session, monkeypatch):
    feed = await _feed(session)
    art = await _article(session, feed, summary_medium="sum")
    monkeypatch.setattr(worker.llm, "is_configured", lambda: True)

    async def boom(s, article, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(worker.ner, "extract_named", boom)
    assert await worker.extract_named_entities_batch() == 1
    await session.refresh(art)
    assert art.ner_extracted_at is not None


async def test_embed_articles_batch_reembeds_stale_input(session, monkeypatch):
    """A current-model vector whose input text has since changed (summary
    arrived after embedding, or the hash predates tracking) is re-embedded."""
    feed = await _feed(session)
    stale = await _article(session, feed, guid="stale", excerpt="body")
    session.add(
        ArticleEmbedding(
            article_id=stale.id,
            model="current",
            embedding=[0.1, 0.2],
            input_hash=worker.embeddings.input_hash_for(stale),
        )
    )
    legacy = await _article(session, feed, guid="legacy", excerpt="body")
    session.add(
        ArticleEmbedding(
            article_id=legacy.id,
            model="current",
            embedding=[0.1, 0.2],
            input_hash=None,
        )
    )
    stale.summary_medium = "a summary arrived later"
    await session.commit()
    monkeypatch.setattr(worker.embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(worker.settings, "openai_embedding_model", "current")

    captured = {}

    async def fake_embed(s, articles):
        captured["ids"] = {a.id for a in articles}
        return len(articles)

    monkeypatch.setattr(worker.embeddings, "embed_articles", fake_embed)
    await worker.embed_articles_batch(feed_id=feed.id)
    assert captured["ids"] == {stale.id, legacy.id}


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
    await _feed(session, url="fresh", last_fetched_at=datetime.now(UTC))

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


async def test_poll_feeds_skips_import_feeds(session, users, monkeypatch):
    user = await users.create(username="importer")
    import_feed = Feed(url=f"newsread://imported/{user.id}", owner_user_id=user.id)
    session.add(import_feed)
    await session.commit()
    await _feed(session, url="due")

    refreshed = []

    async def fake_refresh(s, feed):
        refreshed.append(feed.url)

    async def fake_enrich(ctx):
        pass

    monkeypatch.setattr(worker, "refresh_feed", fake_refresh)
    monkeypatch.setattr(worker, "enrich_and_summarize", fake_enrich)
    await worker.poll_feeds({})
    assert refreshed == ["https://feed/due"]


async def test_poll_feeds_due_by_interval(session, monkeypatch):
    # Fetched long ago relative to its interval -> due.
    feed = Feed(
        url="https://feed/old",
        refresh_interval_minutes=15,
        last_fetched_at=datetime.now(UTC) - timedelta(hours=1),
    )
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
    assert worker.WorkerSettings.functions == [
        worker.enrich_feed,
        worker.send_share_push,
        worker.send_project_pin_push,
    ]
    assert len(worker.WorkerSettings.cron_jobs) == 3


# --- send_share_push ---


async def test_send_share_push_missing_share(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("should not send for a missing share")

    monkeypatch.setattr(worker.push, "send_push", boom)
    await worker.send_share_push({}, 99999)


async def test_send_share_push_notifies_recipients(session, users, monkeypatch):
    from app.models import Share, ShareRecipient

    sender = await users.create(username="sender")
    recipient = await users.create(username="reader")
    feed = await _feed(session, url="share-push")
    art = await _article(session, feed, title="Big News")
    share = Share(from_user_id=sender.id, article_id=art.id, note=None)
    share.recipients = [ShareRecipient(to_user_id=recipient.id)]
    session.add(share)
    await session.commit()
    await session.refresh(share)

    sent = {}

    async def fake_send(user_ids, title, body, data=None):
        sent.update(user_ids=user_ids, title=title, body=body, data=data)
        return len(user_ids)

    monkeypatch.setattr(worker.push, "send_push", fake_send)
    await worker.send_share_push({}, share.id)
    assert sent["user_ids"] == [recipient.id]
    assert "@sender" in sent["title"]
    assert sent["body"] == "Big News"  # no note -> article title
    assert sent["data"]["share_id"] == share.id


async def test_send_share_push_note_becomes_body(session, users, monkeypatch):
    from app.models import Share, ShareRecipient

    sender = await users.create(username="sender2")
    recipient = await users.create(username="reader2")
    feed = await _feed(session, url="share-push-2")
    art = await _article(session, feed, title="Ignored Title")
    share = Share(from_user_id=sender.id, article_id=art.id, note="check this out")
    share.recipients = [ShareRecipient(to_user_id=recipient.id)]
    session.add(share)
    await session.commit()
    await session.refresh(share)

    captured = {}

    async def fake_send(user_ids, title, body, data=None):
        captured["body"] = body
        return 1

    monkeypatch.setattr(worker.push, "send_push", fake_send)
    await worker.send_share_push({}, share.id)
    assert captured["body"] == "check this out"


# --- send_project_pin_push ---


async def _pinned_project(session, users, *, muted=False, shared=True):
    from datetime import datetime

    from app.models import Project, ProjectArticle, ProjectMember

    adder = await users.create(username="pinner")
    member = await users.create(username="watcher")
    feed = await _feed(session, url=f"pin-push-{muted}-{shared}")
    art = await _article(session, feed, title="Pinned News")
    project = Project(owner_id=adder.id, name="Push Proj")
    project.members = [
        ProjectMember(user_id=adder.id, role="owner"),
        ProjectMember(user_id=member.id, role="member", is_muted=muted),
    ]
    session.add(project)
    await session.commit()
    await session.refresh(project)
    pin = ProjectArticle(
        project_id=project.id,
        article_id=art.id,
        added_by_user_id=adder.id,
        is_shared=shared,
        shared_at=datetime.now(UTC) if shared else None,
    )
    session.add(pin)
    await session.commit()
    await session.refresh(pin)
    return adder, member, pin


async def test_send_project_pin_push_missing_pin(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("should not send for a missing pin")

    monkeypatch.setattr(worker.push, "send_push", boom)
    await worker.send_project_pin_push({}, 99999)


async def test_send_project_pin_push_notifies_other_members(session, users, monkeypatch):
    adder, member, pin = await _pinned_project(session, users)
    sent = {}

    async def fake_send(user_ids, title, body, data=None):
        sent.update(user_ids=user_ids, title=title, body=body, data=data)
        return len(user_ids)

    monkeypatch.setattr(worker.push, "send_push", fake_send)
    await worker.send_project_pin_push({}, pin.id)
    assert sent["user_ids"] == [member.id]  # never the adder
    assert "@pinner" in sent["title"] and "Push Proj" in sent["title"]
    assert sent["body"] == "Pinned News"
    assert sent["data"]["project_id"] == pin.project_id


async def test_send_project_pin_push_skips_muted_members(session, users, monkeypatch):
    adder, member, pin = await _pinned_project(session, users, muted=True)
    sent = {}

    async def fake_send(user_ids, title, body, data=None):
        sent["user_ids"] = user_ids
        return len(user_ids)

    monkeypatch.setattr(worker.push, "send_push", fake_send)
    await worker.send_project_pin_push({}, pin.id)
    assert sent["user_ids"] == []


async def test_send_project_pin_push_skips_unpublished_pin(session, users, monkeypatch):
    adder, member, pin = await _pinned_project(session, users, shared=False)

    async def boom(*args, **kwargs):
        raise AssertionError("should not send for a private pin")

    monkeypatch.setattr(worker.push, "send_push", boom)
    await worker.send_project_pin_push({}, pin.id)


async def test_send_project_pin_push_adder_comment_becomes_body(session, users, monkeypatch):
    from app.models import ProjectArticleComment

    adder, member, pin = await _pinned_project(session, users)
    # The adder's latest comment wins; others' comments and link-only ones don't.
    session.add(
        ProjectArticleComment(
            project_id=pin.project_id,
            article_id=pin.article_id,
            author_id=adder.id,
            body="first thought",
        )
    )
    session.add(
        ProjectArticleComment(
            project_id=pin.project_id,
            article_id=pin.article_id,
            author_id=adder.id,
            body="must read",
        )
    )
    session.add(
        ProjectArticleComment(
            project_id=pin.project_id,
            article_id=pin.article_id,
            author_id=adder.id,
            body="",
            link_url="https://youtu.be/x",
        )
    )
    session.add(
        ProjectArticleComment(
            project_id=pin.project_id,
            article_id=pin.article_id,
            author_id=member.id,
            body="someone else's take",
        )
    )
    await session.commit()
    captured = {}

    async def fake_send(user_ids, title, body, data=None):
        captured["body"] = body
        return 1

    monkeypatch.setattr(worker.push, "send_push", fake_send)
    await worker.send_project_pin_push({}, pin.id)
    assert captured["body"] == "must read"
