from datetime import UTC, datetime

from sqlalchemy import func, select

from app import crypto, llm
from app import db as app_db
from app.fetcher import FeedParseError
from app.models import Article, Feed, Subscription
from app.routers import imports as imports_module
from app.routers.imports import normalize_import_url, process_import

LONG_TEXT = "word " * 200


def _record_process(monkeypatch):
    calls = []

    async def fake_process(article_id, user_id, config):
        calls.append((article_id, user_id, config))

    monkeypatch.setattr(imports_module, "process_import", fake_process)
    return calls


async def _import_feed_of(session, user):
    return await session.scalar(select(Feed).where(Feed.owner_user_id == user.id))


# --- URL normalization ---


def test_normalize_import_url():
    assert (
        normalize_import_url("  Example.com/A?utm_source=x&id=2&fbclid=y#frag  ")
        == "https://example.com/A?id=2"
    )
    assert normalize_import_url("http://a.b/c") == "http://a.b/c"


# --- POST /imports ---


async def test_import_creates_hidden_feed_and_article(client, users, session, monkeypatch):
    calls = _record_process(monkeypatch)
    user = await users.create(username="imp")
    resp = await client.post(
        "/api/imports",
        json={"url": "https://example.com/story?utm_source=nl&id=2"},
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["feed_title"] == "Imported"
    assert body["url"] == "https://example.com/story?id=2"
    assert body["title"] == "example.com"  # stub until the page fetch lands

    feed = await _import_feed_of(session, user)
    assert feed is not None and feed.id == body["feed_id"]
    assert feed.image_gen_enabled is False
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.feed_id == feed.id)
    )
    assert sub is not None
    assert calls == [(body["id"], user.id, None)]  # no LLM configured in tests

    # Hidden from feed management, excluded from the inbox, served on its own page.
    feeds = (await client.get("/api/feeds", headers=users.auth(user))).json()
    assert feeds == []
    inbox = (await client.get("/api/articles", headers=users.auth(user))).json()
    assert inbox == []
    page = (await client.get(f"/api/articles?feed_id={feed.id}", headers=users.auth(user))).json()
    assert [a["id"] for a in page] == [body["id"]]
    detail = await client.get(f"/api/articles/{body['id']}", headers=users.auth(user))
    assert detail.status_code == 200


async def test_import_is_idempotent_per_user(client, users, session, monkeypatch):
    _record_process(monkeypatch)
    user = await users.create(username="twice")
    first = await client.post(
        "/api/imports", json={"url": "https://site.example/a"}, headers=users.auth(user)
    )
    assert first.status_code == 201
    # Same page, different tracking params and fragment — dedups to the same row.
    second = await client.post(
        "/api/imports",
        json={"url": "https://site.example/a?utm_medium=mail#top"},
        headers=users.auth(user),
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    count = await session.scalar(select(func.count()).select_from(Article))
    assert count == 1


async def test_import_copies_existing_article(client, users, data, session, monkeypatch):
    calls = _record_process(monkeypatch)
    user = await users.create(username="copier")
    feed = await data.feed()
    source = await data.article(
        feed,
        url="https://news.example/big-story",
        full_text=LONG_TEXT,
        full_text_fetched_at=datetime.now(UTC),
        summary="long summary",
        summary_short="short",
        summary_medium="medium",
        summary_model="m1",
        summary_generated_at=datetime.now(UTC),
    )
    resp = await client.post(
        "/api/imports",
        json={"url": "https://news.example/big-story"},
        headers=users.auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] != source.id
    assert body["title"] == source.title
    assert body["summary"] == "long summary"
    copied = await session.get(Article, body["id"])
    assert copied.full_text == LONG_TEXT
    assert copied.full_text_fetched_at is not None
    assert copied.feed_id == (await _import_feed_of(session, user)).id
    # The background stage still runs (it no-ops on an already-complete copy).
    assert [c[0] for c in calls] == [body["id"]]


async def test_import_rejects_private_url(client, users, monkeypatch):
    async def deny(url):
        raise FeedParseError("private")

    monkeypatch.setattr("app.fetcher._validate_public_url", deny)
    user = await users.create(username="ssrf")
    resp = await client.post(
        "/api/imports", json={"url": "http://10.0.0.5/internal"}, headers=users.auth(user)
    )
    assert resp.status_code == 400
    assert "public" in resp.json()["detail"]


async def test_saved_filter_includes_imports(client, users, session, monkeypatch):
    _record_process(monkeypatch)
    user = await users.create(username="saver")
    body = (
        await client.post(
            "/api/imports", json={"url": "https://keep.example/x"}, headers=users.auth(user)
        )
    ).json()
    resp = await client.post(
        f"/api/articles/{body['id']}/state",
        json={"is_saved": True},
        headers=users.auth(user),
    )
    assert resp.status_code == 200
    saved = (await client.get("/api/articles?filter=saved", headers=users.auth(user))).json()
    assert [a["id"] for a in saved] == [body["id"]]


async def test_share_imported_article(client, users, monkeypatch):
    _record_process(monkeypatch)
    sender = await users.create(username="ishare")
    recipient = await users.create(username="igets")
    body = (
        await client.post(
            "/api/imports", json={"url": "https://pass.example/y"}, headers=users.auth(sender)
        )
    ).json()
    share = await client.post(
        "/api/shares",
        json={"article_id": body["id"], "recipients": ["igets"], "note": "look"},
        headers=users.auth(sender),
    )
    assert share.status_code == 201
    received = (await client.get("/api/shares/received", headers=users.auth(recipient))).json()
    assert [s["article"]["id"] for s in received] == [body["id"]]
    detail = await client.get(f"/api/articles/{body['id']}", headers=users.auth(recipient))
    assert detail.status_code == 200


async def test_import_with_broken_stored_llm_key(client, users, monkeypatch):
    calls = _record_process(monkeypatch)

    async def broken(session, user_id):
        raise crypto.TokenCryptoError("cannot decrypt")

    monkeypatch.setattr(imports_module.llm, "resolve_config", broken)
    user = await users.create(username="badkey")
    resp = await client.post(
        "/api/imports", json={"url": "https://ok.example/z"}, headers=users.auth(user)
    )
    assert resp.status_code == 201  # the import itself must not fail
    assert calls[0][2] is None  # summarization simply runs keyless (i.e. not at all)


async def test_import_feed_creation_race(session, users, monkeypatch):
    # Another request creates the feed between our select and our flush; the
    # unique owner constraint elects them the winner and we adopt their row.
    user = await users.create(username="race")
    # The loser's rollback expires every object in the session — capture the
    # id now so the assertion doesn't lazy-refresh `user` post-rollback.
    user_id = user.id
    real_flush = session.flush

    async def racing_flush():
        async with app_db.SessionLocal() as other:
            other.add(
                Feed(url=f"newsread://imported/{user_id}", title="Imported", owner_user_id=user_id)
            )
            await other.commit()
        await real_flush()

    monkeypatch.setattr(session, "flush", racing_flush)
    feed = await imports_module._import_feed(session, user_id)
    assert feed is not None and feed.owner_user_id == user_id


# --- GET /imports/feed ---


async def test_import_feed_endpoint_is_stable(client, users):
    user = await users.create(username="stable")
    first = (await client.get("/api/imports/feed", headers=users.auth(user))).json()
    second = (await client.get("/api/imports/feed", headers=users.auth(user))).json()
    assert first["feed_id"] == second["feed_id"]


async def test_import_feed_has_no_feed_management(client, users):
    user = await users.create(username="mgmt")
    feed_id = (await client.get("/api/imports/feed", headers=users.auth(user))).json()["feed_id"]
    patch = await client.patch(
        f"/api/feeds/{feed_id}/settings", json={"is_muted": True}, headers=users.auth(user)
    )
    assert patch.status_code == 404
    refresh = await client.post(f"/api/feeds/{feed_id}/refresh", headers=users.auth(user))
    assert refresh.status_code == 404
    delete = await client.delete(f"/api/feeds/{feed_id}", headers=users.auth(user))
    assert delete.status_code == 404


# --- process_import (background stage) ---


async def _stub_import(session, users, **article_kwargs):
    user = await users.create(username=f"bg{datetime.now(UTC).timestamp()}")
    feed = Feed(url=f"newsread://imported/{user.id}", title="Imported", owner_user_id=user.id)
    session.add(feed)
    await session.flush()
    session.add(Subscription(user_id=user.id, feed_id=feed.id))
    defaults = dict(guid="g", url="https://x/page", title="x", content_html="")
    defaults.update(article_kwargs)
    article = Article(feed_id=feed.id, **defaults)
    session.add(article)
    await session.commit()
    await session.refresh(article)
    return user, article


async def test_process_import_fetches_and_stamps(session, users, monkeypatch):
    user, article = await _stub_import(session, users)

    async def fake_fetch(url):
        return LONG_TEXT, "https://x/og.png", "The Real Title"

    monkeypatch.setattr(imports_module, "fetch_page", fake_fetch)
    await process_import(article.id, user.id, None)  # no LLM: fetch only
    await session.refresh(article)
    assert article.title == "The Real Title"
    assert article.full_text == LONG_TEXT
    assert article.excerpt.startswith("word word")
    assert article.image_url == "https://x/og.png"
    assert article.full_text_fetched_at is not None
    assert article.summary == ""


async def test_process_import_summarizes_with_config(session, users, monkeypatch):
    user, article = await _stub_import(session, users)

    async def fake_fetch(url):
        return LONG_TEXT, None, None

    async def fake_generate(inner_session, inner_article, **kwargs):
        inner_article.summary = "S"
        inner_article.summary_short = "s"
        inner_article.summary_medium = "m"
        await inner_session.commit()

    monkeypatch.setattr(imports_module, "fetch_page", fake_fetch)
    monkeypatch.setattr(imports_module, "generate_summaries", fake_generate)
    config = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    await process_import(article.id, user.id, config)
    await session.refresh(article)
    assert article.summary == "S"


async def test_process_import_skips_complete_copy(session, users, monkeypatch):
    user, article = await _stub_import(
        session,
        users,
        full_text=LONG_TEXT,
        full_text_fetched_at=datetime.now(UTC),
        summary="done",
        summary_short="d",
    )

    async def no_fetch(url):
        raise AssertionError("copied rows are never re-fetched")

    monkeypatch.setattr(imports_module, "fetch_page", no_fetch)
    config = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    await process_import(article.id, user.id, config)  # must not call the LLM either


async def test_process_import_summary_skipped_is_terminal(session, users, monkeypatch):
    from app.summarizer import SummarySkipped

    user, article = await _stub_import(session, users)

    async def fake_fetch(url):
        return LONG_TEXT, None, None

    async def skips(*args, **kwargs):
        raise SummarySkipped("too_short")

    monkeypatch.setattr(imports_module, "fetch_page", fake_fetch)
    monkeypatch.setattr(imports_module, "generate_summaries", skips)
    config = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    await process_import(article.id, user.id, config)  # intentional no-summary exit
    await session.refresh(article)
    assert article.summary == ""


async def test_process_import_survives_summary_failure(session, users, monkeypatch):
    user, article = await _stub_import(session, users)

    async def fake_fetch(url):
        return LONG_TEXT, None, None

    async def boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(imports_module, "fetch_page", fake_fetch)
    monkeypatch.setattr(imports_module, "generate_summaries", boom)
    config = llm.LLMConfig(provider="system", api_key="k", base_url=None, model="m")
    await process_import(article.id, user.id, config)  # logs, never raises
    await session.refresh(article)
    assert article.full_text == LONG_TEXT
    assert article.summary == ""


async def test_process_import_gone_article(session):
    await process_import(999999, 1, None)  # deleted before the task ran — no-op
