import types

import pytest
from sqlalchemy import select

from app import db as app_db
from app import embeddings
from app.models import Article, ArticleEmbedding, Feed


def test_is_configured(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "openai_api_key", "k")
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    monkeypatch.setattr(app_db, "vector_enabled", True)
    assert embeddings.is_configured()
    monkeypatch.setattr(app_db, "vector_enabled", False)
    assert not embeddings.is_configured()


def test_text_for_prefers_summary():
    art = Article(title="T", summary_medium="the summary", excerpt="ex", full_text="ft")
    assert embeddings.text_for(art) == "T\n\nthe summary"


def test_text_for_falls_back_to_excerpt_then_fulltext():
    art = Article(title="T", summary_medium="", excerpt="excerpt body", full_text="ft")
    assert "excerpt body" in embeddings.text_for(art)
    art2 = Article(title="T", summary_medium="", excerpt="", full_text="full body text")
    assert "full body text" in embeddings.text_for(art2)


def test_text_for_caps_length():
    art = Article(title="T", summary_medium="x" * 10000, excerpt="", full_text="")
    assert len(embeddings.text_for(art)) <= embeddings.MAX_CHARS


def _fake_client(vectors):
    async def create(**kwargs):
        data = [types.SimpleNamespace(embedding=v) for v in vectors]
        return types.SimpleNamespace(data=data)

    return types.SimpleNamespace(
        embeddings=types.SimpleNamespace(create=create)
    )


async def test_embed_texts(monkeypatch):
    monkeypatch.setattr(embeddings.llm, "get_client", lambda: _fake_client([[0.1, 0.2]]))
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    out = await embeddings.embed_texts(["hi"])
    assert out == [[0.1, 0.2]]


async def test_embed_articles_empty(session):
    assert await embeddings.embed_articles(session, []) == 0


async def _make_article(session):
    feed = Feed(url="https://feed/x")
    session.add(feed)
    await session.flush()
    art = Article(feed_id=feed.id, guid="g", url="https://x/a", title="T", excerpt="body")
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


async def test_embed_articles_upserts(session, monkeypatch):
    art = await _make_article(session)
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb-model")
    monkeypatch.setattr(
        embeddings, "embed_texts",
        lambda texts: _returns([[0.5] * 4 for _ in texts]),
    )
    n = await embeddings.embed_articles(session, [art])
    assert n == 1
    row = await session.scalar(
        select(ArticleEmbedding).where(ArticleEmbedding.article_id == art.id)
    )
    assert row.model == "emb-model"

    # Re-embed updates in place (on_conflict_do_update).
    monkeypatch.setattr(
        embeddings, "embed_texts",
        lambda texts: _returns([[0.9] * 4 for _ in texts]),
    )
    n2 = await embeddings.embed_articles(session, [art])
    assert n2 == 1


async def _returns(value):
    return value
