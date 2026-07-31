"""Instance metrics (#115): daily-active tracking, processing events, and
system-key LLM metering in the worker."""

import asyncio
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app import user_activity, worker
from app.models import ArticleProcessingEvent, LLMUsage, UserActivityDay
from app.processing_events import add_event, record_event
from app.summarizer import SummarySkipped

# --- user_activity ---


async def test_authenticated_request_records_activity_day(client, users, session):
    user = await users.create(username="active")
    resp = await client.get("/api/auth/me", headers=users.auth(user))
    assert resp.status_code == 200
    rows = (await session.scalars(select(UserActivityDay))).all()
    assert [(r.user_id, r.day) for r in rows] == [(user.id, datetime.now(UTC).date())]


async def test_activity_writes_are_throttled(client, users, session, monkeypatch):
    user = await users.create(username="busy")
    writes = []
    original = user_activity._should_write

    def counting(user_id, now):
        decision = original(user_id, now)
        writes.append(decision)
        return decision

    monkeypatch.setattr(user_activity, "_should_write", counting)
    for _ in range(3):
        await client.get("/api/auth/me", headers=users.auth(user))
    # First request writes; the two others hit the throttle cache.
    assert writes == [True, False, False]
    assert len((await session.scalars(select(UserActivityDay))).all()) == 1


async def test_activity_day_rollover_writes_again(users, session):
    user = await users.create(username="nightowl")
    await user_activity.record(user.id)
    # Simulate the previous write having happened yesterday, well within the
    # hourly throttle window: the UTC day change must still force a write.
    yesterday = datetime.now(UTC) - timedelta(minutes=5)
    user_activity._recorded[user.id] = (yesterday.date() - timedelta(days=1), yesterday)
    await user_activity.record(user.id)
    days = (await session.scalars(select(UserActivityDay.day))).all()
    assert len(days) == 1  # yesterday's simulated day was never actually inserted
    assert days == [datetime.now(UTC).date()]


async def test_activity_upsert_is_conflict_safe(users, session):
    user = await users.create(username="dupe")
    await user_activity.record(user.id)
    user_activity.reset()
    await user_activity.record(user.id)  # same (user, day): ON CONFLICT DO NOTHING
    assert len((await session.scalars(select(UserActivityDay))).all()) == 1


async def test_activity_record_never_raises(users, monkeypatch):
    user = await users.create(username="fragile")

    def explode():
        raise RuntimeError("db down")

    monkeypatch.setattr(user_activity.db, "SessionLocal", explode)
    await user_activity.record(user.id)  # no raise
    # The failed write must not poison the throttle cache: once the DB is
    # back, the next call writes.
    assert user_activity._should_write(user.id, datetime.now(UTC))


# --- processing events ---


async def test_add_event_truncates_detail(session, users, data):
    feed = await data.feed()
    add_event(session, stage="enrich", outcome="failed", feed_id=feed.id, detail="x" * 500)
    await session.commit()
    row = (await session.scalars(select(ArticleProcessingEvent))).one()
    assert (row.stage, row.outcome, row.feed_id) == ("enrich", "failed", feed.id)
    assert len(row.detail) == 120


async def test_record_event_own_session_never_raises(session, monkeypatch):
    await record_event(stage="poll", outcome="failed", detail="FetchError")
    row = (await session.scalars(select(ArticleProcessingEvent))).one()
    assert (row.stage, row.detail) == ("poll", "FetchError")

    from app import processing_events as pe

    monkeypatch.setattr(pe.db, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError))
    await record_event(stage="poll", outcome="failed")  # no raise


async def test_summary_skip_writes_dated_event(session, users, data):
    from app.summarizer import generate_summaries

    feed = await data.feed()
    art = await data.article(
        feed, full_text="short", full_text_fetched_at=datetime.now(UTC), excerpt="", content_html=""
    )
    try:
        await generate_summaries(session, art, allow_refetch=False)
        raise AssertionError("expected SummarySkipped")
    except SummarySkipped:
        pass
    event = (await session.scalars(select(ArticleProcessingEvent))).one()
    assert (event.stage, event.outcome, event.detail) == ("summarize", "skipped", "too_short")
    assert (event.article_id, event.feed_id) == (art.id, feed.id)


async def test_for_each_article_failure_writes_event(session, users, data):
    feed = await data.feed()
    art = await data.article(feed)

    async def boom(s, article):
        raise RuntimeError("enrich failed")

    await worker._for_each_article(
        [art.id], gate=asyncio.Semaphore(1), label="Enrichment", stage="enrich", fn=boom
    )
    event = (await session.scalars(select(ArticleProcessingEvent))).one()
    assert (event.stage, event.outcome, event.article_id) == ("enrich", "failed", art.id)
    assert event.detail == "RuntimeError"


# --- worker batch metering ---


async def test_batch_summary_metered_as_system_overhead(session, users, data, monkeypatch):
    feed = await data.feed()
    art = await data.article(feed)

    async def fake_generate(s, article, allow_refetch=False, usage=None):
        if usage is not None:
            usage.add(100, 20)
        article.summary_short = "s"

    monkeypatch.setattr(worker, "generate_summaries", fake_generate)
    await worker._for_each_article(
        [art.id],
        gate=asyncio.Semaphore(1),
        label="Auto-summary",
        stage="summarize",
        fn=worker._summarize_quietly,
    )
    row = (await session.scalars(select(LLMUsage))).one()
    assert (row.billing_source, row.user_id, row.feature) == ("system", None, "summary")
    assert (row.prompt_tokens, row.completion_tokens, row.status) == (100, 20, "ok")


async def test_batch_summary_skip_records_no_usage(session, users, data, monkeypatch):
    feed = await data.feed()
    art = await data.article(feed)

    async def skip(s, article, allow_refetch=False, usage=None):
        raise SummarySkipped()

    monkeypatch.setattr(worker, "generate_summaries", skip)
    await worker._for_each_article(
        [art.id],
        gate=asyncio.Semaphore(1),
        label="Auto-summary",
        stage="summarize",
        fn=worker._summarize_quietly,
    )
    assert (await session.scalars(select(LLMUsage))).all() == []


async def test_ner_batch_metered_and_failures_evented(session, users, data, monkeypatch):
    feed = await data.feed()
    art = await data.article(feed, summary_medium="a medium summary")

    async def fake_extract(s, article, *, config=None, usage=None):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker.ner, "extract_named", fake_extract)
    await worker._for_each_article(
        [art.id], gate=asyncio.Semaphore(1), label="Entity tagging", stage="ner", fn=worker._ner_one
    )
    usage_row = (await session.scalars(select(LLMUsage))).one()
    assert (usage_row.feature, usage_row.billing_source, usage_row.status) == (
        "ner",
        "system",
        "error",
    )
    event = (await session.scalars(select(ArticleProcessingEvent))).one()
    assert (event.stage, event.outcome, event.detail) == ("ner", "failed", "RuntimeError")
    await session.refresh(art)
    assert art.ner_extracted_at is not None  # still stamped: no retry loop


async def test_activity_day_model_shape():
    today = date(2026, 8, 1)
    row = UserActivityDay(user_id=1, day=today)
    assert (row.user_id, row.day) == (1, today)
