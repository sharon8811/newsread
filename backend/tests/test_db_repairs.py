from datetime import UTC, datetime

from sqlalchemy import select

from app import db
from app.models import Article, ArticleEntity, Entity, Feed


async def test_unescape_plain_text_entities_repairs_titles_excerpts_and_feeds(session):
    feed = Feed(url="https://feed/entity-repair", description="News &amp; analysis")
    clean_feed = Feed(url="https://feed/entity-clean", description="Plain description")
    session.add_all([feed, clean_feed])
    await session.flush()
    damaged = Article(
        feed_id=feed.id,
        guid="damaged",
        url="https://example.com/sp",
        title="S&amp;P downgrades Oracle to BBB &#8211; one notch above junk",
        excerpt="Q&amp;A about the &lt;cloud&gt;",
    )
    untouched = Article(
        feed_id=feed.id,
        guid="untouched",
        url="https://example.com/plain",
        title="Cats & dogs live together",
        excerpt="No entities here, not even AT&T-style ampersands alone",
    )
    session.add_all([damaged, untouched])
    await session.commit()

    await db._unescape_plain_text_entities(session)
    await session.commit()
    await session.refresh(damaged)
    await session.refresh(untouched)
    await session.refresh(feed)
    await session.refresh(clean_feed)

    assert damaged.title == "S&P downgrades Oracle to BBB – one notch above junk"
    assert damaged.excerpt == "Q&A about the <cloud>"
    assert untouched.title == "Cats & dogs live together"
    assert feed.description == "News & analysis"
    assert clean_feed.description == "Plain description"


async def test_skip_existing_short_summaries_preserves_visual_summary_and_entities(session):
    feed = Feed(url="https://feed/summary-repair")
    session.add(feed)
    await session.flush()
    now = datetime.now(UTC)
    short = Article(
        feed_id=feed.id,
        guid="short",
        url="https://reddit.com/r/programming/short",
        title="Seed7 released",
        content_html="<p>Seed7 is a GPL-licensed programming language.</p>",
        summary_short="longer than source",
        summary_medium="medium summary",
        summary="full summary",
        summary_model="model",
        summary_generated_at=now,
    )
    visual = Article(
        feed_id=feed.id,
        guid="visual",
        url="https://map.example",
        title="Live map",
        full_text="You need to enable JavaScript to run this app.",
        summary_short="map gist",
        summary_medium="map paragraph",
        summary="useful visual summary",
        summary_model="vision-model",
        summary_generated_at=now,
    )
    entity = Entity(kind="product", canonical_key="seed7", url="", data={"name": "Seed7"})
    session.add_all([short, visual, entity])
    await session.flush()
    session.add(ArticleEntity(article_id=short.id, entity_id=entity.id, source="ner", position=0))
    await session.commit()

    await db._skip_existing_short_summaries(session)
    await session.commit()
    await session.refresh(short)
    await session.refresh(visual)

    assert short.summary == ""
    assert short.summary_model is None
    assert short.summary_generated_at is None
    assert short.summary_skipped_reason == "too_short"
    assert visual.summary == "useful visual summary"
    assert visual.summary_skipped_reason is None
    assert await session.scalar(select(ArticleEntity).where(ArticleEntity.article_id == short.id))


async def test_clear_error_page_summaries_resets_only_failure_prose(session):
    from sqlalchemy import text as sql_text

    feed = Feed(url="https://feed/error-page-summaries")
    session.add(feed)
    await session.flush()
    now = datetime.now(UTC)
    garbage = Article(
        feed_id=feed.id,
        guid="garbage",
        url="https://example.com/dead",
        title="DeepSeek V4 Flash",
        summary_short="The URL is broken",
        summary_medium="The link 404s",
        summary=(
            "The provided URL leads to a dead end rather than the promised "
            "analysis. The page renders a standard 404 error screen, "
            "indicating the content is missing or the link is invalid."
        ),
        summary_model="vllm/some-model",
        summary_generated_at=now,
        summary_language="English",
    )
    legit = Article(
        feed_id=feed.id,
        guid="legit",
        url="https://example.com/fine",
        title="OpenAI launches",
        summary_short="OpenAI launched a model.",
        summary_medium="OpenAI launched a new model this week.",
        summary="OpenAI launches a new model with better latency and pricing for developers.",
        summary_model="vllm/some-model",
        summary_generated_at=now,
        summary_language="English",
    )
    session.add_all([garbage, legit])
    await session.commit()

    for statement in db.ONE_SHOT_MIGRATIONS["clear_error_page_summaries"]:
        await session.execute(sql_text(statement))
    await session.commit()
    await session.refresh(garbage)
    await session.refresh(legit)

    # Cleared back to "never summarized": the worker regenerates under the
    # UNUSABLE prompt, which either succeeds or stamps unusable_page.
    assert garbage.summary == ""
    assert garbage.summary_short == ""
    assert garbage.summary_model is None
    assert garbage.summary_generated_at is None
    assert garbage.summary_language is None
    assert garbage.summary_skipped_reason is None
    assert legit.summary.startswith("OpenAI launches a new model")
    assert legit.summary_model == "vllm/some-model"


async def test_reprocess_binary_full_text_resets_only_the_mojibake(session):
    """The sweep that catches a PDF read as text when it doesn't even start
    with the signature — trafilatura often begins mid-stream, so the only tell
    left is the decode damage: text decoded from binary is roughly half U+FFFD
    replacement characters, real prose has none."""
    from sqlalchemy import text as sql_text

    feed = Feed(url="https://feed/binary-repair")
    session.add(feed)
    await session.flush()
    now = datetime.now(UTC)
    mojibake = Article(
        feed_id=feed.id,
        guid="mojibake",
        url="https://www.gamedevs.org/uploads/introduction-to-data-oriented-design.pdf",
        title="Introduction to Data-Oriented Design",
        # Half replacement characters, and no "%PDF-" or "endobj" to match on.
        full_text=("�" * 300) + ("x" * 300),
        full_text_fetched_at=now,
        summary_short="La fonta materialo estas teknika eraro.",
        summary_medium="La fonta materialo estas teknika eraro, ne artikolo.",
        # Lingua reads a language off the mojibake, so the model wrote the
        # summary in it — this really happened, in Esperanto.
        summary="La fonta materialo estas teknika eraro, ne artikolo.",
        summary_model="vllm/some-model",
        summary_generated_at=now,
        summary_language="Esperanto",
    )
    prose = Article(
        feed_id=feed.id,
        guid="prose",
        url="https://www.ti.com/lit/eb/slyy228/slyy228.pdf",
        title="USB Type-C",
        full_text="Introduction USB Type-C® is an industry-standard connector. " * 20,
        full_text_fetched_at=now,
        summary="A real summary of a real document.",
        summary_model="vllm/some-model",
        summary_generated_at=now,
    )
    # Refused by the old size cap: empty, stamped, no summary to protect.
    oversized = Article(
        feed_id=feed.id,
        guid="oversized",
        url="https://pagedout.institute/download/PagedOut_009.pdf",
        title="Paged Out! #9",
        full_text="",
        full_text_fetched_at=now,
    )
    session.add_all([mojibake, prose, oversized])
    await session.commit()

    for statement in db.ONE_SHOT_MIGRATIONS["reprocess_binary_full_text"]:
        await session.execute(sql_text(statement))
    await session.commit()
    for article in (mojibake, prose, oversized):
        await session.refresh(article)

    # Back to "never fetched": the worker re-enriches through the PDF branch.
    assert mojibake.full_text == ""
    assert mojibake.full_text_fetched_at is None
    assert mojibake.summary == ""
    assert mojibake.summary_language is None
    assert mojibake.summary_skipped_reason is None
    # A document that yielded real prose keeps it, and keeps its summary.
    assert prose.full_text.startswith("Introduction USB Type-C")
    assert prose.full_text_fetched_at is not None
    assert prose.summary == "A real summary of a real document."
    # And the one the cap turned away is queued for another attempt.
    assert oversized.full_text_fetched_at is None
