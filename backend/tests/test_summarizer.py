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

    async def fake_summarize(title, text, **kwargs):
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

    async def fake_summarize(title, text, **kwargs):
        return ("", "", "")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)
    with pytest.raises(RuntimeError):
        await generate_summaries(session, art)


def _vision_config(supports_vision=True):
    from app import llm

    return llm.LLMConfig(provider="openai", api_key="sk-x", base_url=None,
                         model="gpt-5", user_owned=True, supports_vision=supports_vision)


async def test_thin_with_vision_summarizes_from_screenshot(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    async def fake_capture(url):
        return b"jpeg"

    async def fake_summarize_screenshot(title, shot, **kwargs):
        assert shot == b"jpeg"
        return ("short", "medium", "full from image")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    monkeypatch.setattr(summarizer.llm, "summarize_screenshot", fake_summarize_screenshot)

    await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
    assert art.summary == "full from image"
    assert art.summary_model == "gpt-5"


async def test_thin_without_vision_capability_raises(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    async def fail_capture(url):  # pragma: no cover - must not be reached
        raise AssertionError("screenshot attempted without a vision model")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fail_capture)
    with pytest.raises(ThinContentError):
        await generate_summaries(
            session, art, config=_vision_config(supports_vision=False), allow_vision=True
        )


async def test_thin_batch_path_never_screenshots(session, monkeypatch):
    """allow_vision defaults off: the worker keeps today's cheap behavior."""
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    async def fail_capture(url):  # pragma: no cover - must not be reached
        raise AssertionError("batch path attempted a screenshot")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fail_capture)
    monkeypatch.setattr(summarizer.settings, "openai_model_vision", True)
    with pytest.raises(ThinContentError):
        await generate_summaries(session, art, config=_vision_config())


async def test_thin_system_config_uses_env_vision_flag(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    async def fake_capture(url):
        return b"jpeg"

    async def fake_summarize_screenshot(title, shot, **kwargs):
        return ("s", "m", "f")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    monkeypatch.setattr(summarizer.llm, "summarize_screenshot", fake_summarize_screenshot)
    monkeypatch.setattr(summarizer.settings, "openai_model_vision", True)
    monkeypatch.setattr(summarizer.settings, "openai_model", "sys-model")

    await generate_summaries(session, art, allow_vision=True)
    assert art.summary == "f"
    assert art.summary_model == "sys-model"


async def test_thin_screenshot_failure_raises_thin(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "tiny"

    async def fake_capture(url):
        return None

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    with pytest.raises(ThinContentError):
        await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
