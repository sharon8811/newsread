from sqlalchemy import select

from app import ner
from app.models import Article, ArticleEntity, Entity, Feed


async def _article(session, **kwargs):
    feed = Feed(url=f"https://feed/{kwargs.get('guid', 'x')}")
    session.add(feed)
    await session.flush()
    defaults = dict(
        guid="g", url="https://x/a", title="T", excerpt="", full_text="", summary_medium=""
    )
    defaults.update(kwargs)
    art = Article(feed_id=feed.id, **defaults)
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


def test_body_for_prefers_summary():
    art = Article(title="T", summary_medium="sum", full_text="ft", excerpt="ex")
    assert ner.body_for(art) == "sum"
    art.summary_medium = ""
    assert ner.body_for(art) == "ft"
    art.full_text = ""
    assert ner.body_for(art) == "ex"


async def test_extract_named_creates_and_links(session, monkeypatch):
    art = await _article(session)

    async def fake_entities(title, text, **kwargs):
        return [("person", "Sam Altman"), ("org", "OpenAI"), ("weird", "Skipped")]

    monkeypatch.setattr(ner.llm, "named_entities", fake_entities)
    assert await ner.extract_named(session, art) == 2
    await session.commit()

    entities = (await session.scalars(select(Entity).order_by(Entity.id))).all()
    assert [(e.kind, e.canonical_key, e.data["name"]) for e in entities] == [
        ("person", "sam altman", "Sam Altman"),
        ("org", "openai", "OpenAI"),
    ]
    links = (await session.scalars(select(ArticleEntity))).all()
    assert all(link.source == "ner" for link in links)


async def test_extract_named_reuses_entity_and_is_idempotent(session, monkeypatch):
    art1 = await _article(session, guid="a1")
    art2 = await _article(session, guid="a2")

    async def fake_entities(title, text, **kwargs):
        return [("org", "OpenAI")]

    monkeypatch.setattr(ner.llm, "named_entities", fake_entities)
    await ner.extract_named(session, art1)
    await ner.extract_named(session, art2)
    await ner.extract_named(session, art2)  # re-run: no duplicate link
    await session.commit()

    assert len((await session.scalars(select(Entity))).all()) == 1
    assert len((await session.scalars(select(ArticleEntity))).all()) == 2
