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
