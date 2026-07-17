from datetime import UTC, datetime

from sqlalchemy import select

from app import db
from app.models import Article, ArticleEntity, Entity, Feed


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
