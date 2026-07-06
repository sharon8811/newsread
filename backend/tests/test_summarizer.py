import pytest

from app import summarizer
from app.models import Article, Feed
from app.summarizer import ThinContentError, generate_summaries


async def _make_article(session, **kwargs):
    feed = Feed(url="https://feed/x")
    session.add(feed)
    await session.flush()
    art = Article(feed_id=feed.id, guid="g", url="https://x/a", title="Title",
                  content_html=kwargs.get("content_html", ""))
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


async def test_generate_summaries_success(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text):
        return ("short one", "medium two", "full three")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)
    monkeypatch.setattr(summarizer.settings, "openai_model", "test-model")

    await generate_summaries(session, art)
    assert art.summary_short == "short one"
    assert art.summary_medium == "medium two"
    assert art.summary == "full three"
    assert art.summary_model == "test-model"
    assert art.summary_generated_at is not None


async def test_generate_summaries_thin_raises(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    with pytest.raises(ThinContentError):
        await generate_summaries(session, art)


async def test_generate_summaries_empty_summary_raises(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text):
        return ("", "", "")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)
    with pytest.raises(RuntimeError):
        await generate_summaries(session, art)
