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


def test_text_for_skips_hn_metadata_excerpt():
    # The hnrss-derived excerpt is shared boilerplate, not content: it must
    # never become the embedding body (it made every HN article a "related"
    # hub). Fall through to full text, or to title-only.
    art = Article(
        title="T",
        summary_medium="",
        excerpt="5 points · 3 comments · via Hacker News",
        full_text="real body",
    )
    assert embeddings.text_for(art) == "T\n\nreal body"
    art.full_text = ""
    assert embeddings.text_for(art) == "T\n\n"
    art.excerpt = "12 points · via Hacker News"
    assert embeddings.text_for(art) == "T\n\n"
    # An excerpt that merely mentions the pattern inside prose is real content.
    art.excerpt = "It got 5 points · 3 comments · via Hacker News yesterday"
    assert "5 points" in embeddings.text_for(art)


def _fake_client(vectors):
    async def create(**kwargs):
        data = [types.SimpleNamespace(embedding=v) for v in vectors]
        return types.SimpleNamespace(data=data)

    return types.SimpleNamespace(embeddings=types.SimpleNamespace(create=create))


async def test_embed_texts(monkeypatch):
    monkeypatch.setattr(embeddings.llm, "get_client", lambda: _fake_client([[0.1, 0.2]]))
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    out = await embeddings.embed_texts(["hi"])
    assert out == [[0.1, 0.2]]


def _recording_client(payloads, fail_over: int | None = None):
    """Client that records every input it is sent, and rejects a request whose
    longest text exceeds fail_over the way an over-context provider does."""

    async def create(**kwargs):
        payloads.append(kwargs["input"])
        if fail_over is not None and max(len(text) for text in kwargs["input"]) > fail_over:
            raise RuntimeError(
                "Error code: 400 - {'error': {'message': "
                "'the input length exceeds the context length'}}"
            )
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(embedding=[0.1, 0.2]) for _ in kwargs["input"]]
        )

    return types.SimpleNamespace(embeddings=types.SimpleNamespace(create=create))


async def test_embed_texts_caps_input_at_the_context_budget(monkeypatch):
    payloads = []
    monkeypatch.setattr(embeddings.llm, "get_client", lambda: _recording_client(payloads))
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    monkeypatch.setattr(embeddings.settings, "embedding_input_max_chars", 100)
    await embeddings.embed_texts(["y" * 5000, "short"])
    assert payloads == [["y" * 100, "short"]]


async def test_embed_texts_halves_until_it_fits(monkeypatch):
    """A char budget only approximates a token window — dense scripts and code
    blow past it. Retrying smaller keeps a shortened vector where the article
    would otherwise stay unsearchable."""
    payloads = []
    monkeypatch.setattr(
        embeddings.llm, "get_client", lambda: _recording_client(payloads, fail_over=300)
    )
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    monkeypatch.setattr(embeddings.settings, "embedding_input_max_chars", 1200)
    out = await embeddings.embed_texts(["y" * 5000])
    assert out == [[0.1, 0.2]]
    assert [len(payload[0]) for payload in payloads] == [1200, 600, 300]


async def test_embed_texts_gives_up_below_the_floor(monkeypatch):
    payloads = []
    monkeypatch.setattr(
        embeddings.llm, "get_client", lambda: _recording_client(payloads, fail_over=0)
    )
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    monkeypatch.setattr(embeddings.settings, "embedding_input_max_chars", 400)
    with pytest.raises(RuntimeError):
        await embeddings.embed_texts(["y" * 5000])
    assert [len(payload[0]) for payload in payloads] == [400, 200]


async def test_embed_texts_does_not_retry_other_errors(monkeypatch):
    payloads = []

    async def create(**kwargs):
        payloads.append(kwargs["input"])
        raise RuntimeError("Error code: 401 - invalid api key")

    monkeypatch.setattr(
        embeddings.llm,
        "get_client",
        lambda: types.SimpleNamespace(embeddings=types.SimpleNamespace(create=create)),
    )
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    monkeypatch.setattr(embeddings.settings, "embedding_input_max_chars", 1200)
    with pytest.raises(RuntimeError):
        await embeddings.embed_texts(["hi"])
    assert len(payloads) == 1


async def test_embed_query_caches_normalized_text(monkeypatch):
    embeddings._query_cache.clear()
    calls = []

    async def fake_embed(texts):
        calls.append(texts)
        return [[0.3, 0.7]]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb")
    assert await embeddings.embed_query("  Climate   NEWS ") == [0.3, 0.7]
    assert await embeddings.embed_query("climate news") == [0.3, 0.7]
    assert calls == [["climate news"]]


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
        embeddings,
        "embed_texts",
        lambda texts, **_: _returns([[0.5] * 4 for _ in texts]),
    )
    n = await embeddings.embed_articles(session, [art])
    assert n == 1
    row = await session.scalar(
        select(ArticleEmbedding).where(ArticleEmbedding.article_id == art.id)
    )
    assert row.model == "emb-model"

    # Re-embed updates in place (on_conflict_do_update).
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts, **_: _returns([[0.9] * 4 for _ in texts]),
    )
    n2 = await embeddings.embed_articles(session, [art])
    assert n2 == 1


async def test_embed_articles_isolates_an_article_the_provider_rejects(session, monkeypatch):
    """One article the provider will not accept must not cost the rest of the
    batch its vectors: the batch is re-picked newest-first every refresh, so a
    poisoned batch never drains."""
    feed = Feed(url="https://feed/poison")
    session.add(feed)
    await session.flush()
    articles = []
    for i, title in enumerate(["good one", "poison", "good two"]):
        art = Article(
            feed_id=feed.id, guid=f"p{i}", url=f"https://x/p{i}", title=title, excerpt="body"
        )
        session.add(art)
        articles.append(art)
    await session.commit()
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb-model")

    calls = []

    async def fake_embed_texts(texts, *, shrink=True):
        calls.append((len(texts), shrink))
        if any("poison" in text for text in texts):
            raise RuntimeError("Error code: 400 - the input length exceeds the context length")
        return [[0.5] * 4 for _ in texts]

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)

    assert await embeddings.embed_articles(session, articles) == 2
    # The batch attempt must not shrink: that would cost all three articles
    # text to fit the one the provider refused. Only the retries may.
    assert calls == [(3, False), (1, True), (1, True), (1, True)]
    embedded = set(
        await session.scalars(
            select(ArticleEmbedding.article_id).where(
                ArticleEmbedding.article_id.in_([art.id for art in articles])
            )
        )
    )
    assert embedded == {articles[0].id, articles[2].id}


async def test_embed_articles_writes_nothing_when_every_article_fails(session, monkeypatch):
    art = await _make_article(session)
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb-model")

    async def boom(texts, **_):
        raise RuntimeError("Error code: 400 - the input length exceeds the context length")

    monkeypatch.setattr(embeddings, "embed_texts", boom)
    assert await embeddings.embed_articles(session, [art]) == 0
    assert (
        await session.scalar(select(ArticleEmbedding).where(ArticleEmbedding.article_id == art.id))
        is None
    )


async def test_embed_articles_does_not_split_a_batch_over_a_transient_failure(session, monkeypatch):
    """Only length is an article's own fault. Splitting a batch over a 401 or a
    429 would turn one bad moment into fifty more requests."""
    art = await _make_article(session)
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb-model")
    calls = []

    async def boom(texts, **_):
        calls.append(len(texts))
        raise RuntimeError("Error code: 429 - rate limit exceeded")

    monkeypatch.setattr(embeddings, "embed_texts", boom)
    with pytest.raises(RuntimeError):
        await embeddings.embed_articles(session, [art])
    assert calls == [1]


async def test_embed_articles_stores_input_hash(session, monkeypatch):
    art = await _make_article(session)
    monkeypatch.setattr(embeddings.settings, "openai_embedding_model", "emb-model")
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts, **_: _returns([[0.5] * 4 for _ in texts]),
    )
    await embeddings.embed_articles(session, [art])
    row = await session.scalar(
        select(ArticleEmbedding).where(ArticleEmbedding.article_id == art.id)
    )
    assert row.input_hash == embeddings.input_hash_for(art)


async def test_stale_input_sql_matches_text_for(session, monkeypatch):
    """stale_input() recomputes input_hash_for() in SQL; any drift between the
    two would either miss stale vectors or re-embed fresh ones forever. Pin
    the parity across the fallback branches and truncation/unicode edges."""
    feed = Feed(url="https://feed/hash-parity")
    session.add(feed)
    await session.flush()
    cases = [
        dict(summary_medium="the summary", excerpt="ex", full_text="ft"),
        dict(summary_medium="", excerpt="just an excerpt", full_text="ft"),
        dict(summary_medium="", excerpt="", full_text="full body text"),
        dict(summary_medium="", excerpt="", full_text=""),
        dict(
            summary_medium="",
            excerpt="5 points · 3 comments · via Hacker News",
            full_text="real body",
        ),
        dict(summary_medium="", excerpt="12 points · via Hacker News", full_text=""),
        dict(summary_medium="", excerpt="", full_text="y" * 9000),
        dict(summary_medium="x" * 10000, excerpt="", full_text=""),
        dict(summary_medium="Sömé ünïcode ✓ · テスト", excerpt="", full_text=""),
    ]
    articles = []
    for i, fields in enumerate(cases):
        art = Article(
            feed_id=feed.id, guid=f"hp{i}", url=f"https://x/{i}", title=f"Title {i}", **fields
        )
        session.add(art)
        articles.append(art)
    await session.flush()
    for art in articles:
        session.add(
            ArticleEmbedding(
                article_id=art.id,
                model="emb",
                embedding=[0.1, 0.2],
                input_hash=embeddings.input_hash_for(art),
            )
        )
    await session.commit()

    stale_ids = set(
        await session.scalars(
            select(Article.id)
            .join(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
            .where(embeddings.stale_input())
        )
    )
    assert stale_ids == set()

    # Text changes flip exactly the touched article to stale.
    articles[1].summary_medium = "a summary arrived later"
    await session.commit()
    stale_ids = set(
        await session.scalars(
            select(Article.id)
            .join(ArticleEmbedding, ArticleEmbedding.article_id == Article.id)
            .where(embeddings.stale_input())
        )
    )
    assert stale_ids == {articles[1].id}


async def _returns(value):
    return value
