from datetime import UTC, datetime

import pytest

from app import summarizer
from app.models import Article, Feed
from app.summarizer import SummarySkipped, ThinContentError, generate_summaries


async def _make_article(session, **kwargs):
    feed = Feed(url="https://feed/x", summary_instructions=kwargs.get("summary_instructions"))
    session.add(feed)
    await session.flush()
    art = Article(
        feed_id=feed.id,
        guid="g",
        url=kwargs.get("url", "https://x/a"),
        title="Title",
        content_html=kwargs.get("content_html", ""),
        full_text=kwargs.get("full_text", ""),
        full_text_fetched_at=kwargs.get("full_text_fetched_at"),
    )
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
    art.summary_skipped_reason = "too_short"

    await generate_summaries(session, art)
    assert art.summary_short == "short one"
    assert art.summary_medium == "medium two"
    assert art.summary == "full three"
    assert art.summary_model == "test-model"
    assert art.summary_generated_at is not None
    assert art.summary_skipped_reason is None


async def test_generate_summaries_stamps_the_summary_language(session, monkeypatch):
    """Detected once, at generation time, so the translate action can tell a
    Hebrew summary from an English one without paying for a model call."""
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        return ("קצר", "בינוני", "ההצבעה בכנסת נדחתה בשבוע, והקואליציה מחפשת רוב חדש.")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    await generate_summaries(session, art)
    assert art.summary_language == "Hebrew"


async def test_generate_summaries_passes_feed_instructions_and_transcript_kind(
    session, monkeypatch
):
    art = await _make_article(
        session,
        url="https://www.youtube.com/watch?v=RsR6cbovMfI",
        full_text="spoken words",
        full_text_fetched_at=datetime.now(UTC),
        summary_instructions="Skip sponsor segments.",
    )
    captured = {}

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        captured.update(kwargs)
        return ("s", "m", "f")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    await generate_summaries(session, art)
    assert captured["instructions"] == "Skip sponsor segments."
    assert captured["source_kind"] == "transcript"


async def test_generate_summaries_refuses_a_video_whose_captions_are_still_owed(
    session, monkeypatch
):
    # Enrichment leaves a blocked video unstamped. Whatever we store now is
    # permanent — a summary is never regenerated, and "too_short" would block
    # even a manual retry — so both the on-demand path and the importer must
    # back off instead of summarizing the description.
    art = await _make_article(session, url="https://www.youtube.com/watch?v=RsR6cbovMfI")
    assert art.full_text_fetched_at is None

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("summarized a video whose transcript is still owed")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fail)

    with pytest.raises(ThinContentError, match="throttling caption requests"):
        await generate_summaries(session, art, allow_vision=True, config=_vision_config())
    assert art.summary_short == ""
    assert art.summary_skipped_reason is None  # nothing permanent recorded


async def test_generate_summaries_treats_a_captionless_video_as_an_article(session, monkeypatch):
    # No transcript means the text is the feed's own description, which reads
    # like prose — the transcript caveats would be a lie. The stamp is what
    # separates "this video has no captions" from "YouTube refused us".
    art = await _make_article(
        session,
        url="https://www.youtube.com/watch?v=RsR6cbovMfI",
        full_text_fetched_at=datetime.now(UTC),
    )
    captured = {}

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        captured.update(kwargs)
        return ("s", "m", "f")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    await generate_summaries(session, art)
    assert captured["source_kind"] == "article"
    assert captured["instructions"] is None


async def test_generate_summaries_says_a_video_has_no_captions(session, monkeypatch):
    # Captions off and the entry carries no description either. "too_short"
    # would tell the reader the post is short, and the screenshot fallback
    # would render a video player — so the status names what happened.
    art = await _make_article(
        session,
        url="https://www.youtube.com/watch?v=RsR6cbovMfI",
        full_text_fetched_at=datetime.now(UTC),
    )

    async def fake_ensure(session_, article, allow_refetch=True):
        return ""

    async def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("a caption-less video attempted an LLM or screenshot call")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fail)
    monkeypatch.setattr(summarizer.screenshot, "capture", fail)

    with pytest.raises(SummarySkipped):
        await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
    assert art.summary_skipped_reason == "no_transcript"


async def test_generate_summaries_says_a_pdf_could_not_be_read(session, monkeypatch):
    art = await _make_article(
        session, url="https://x/paper.pdf", full_text_fetched_at=datetime.now(UTC)
    )

    async def fake_ensure(session_, article, allow_refetch=True):
        return ""

    async def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("an unreadable document attempted an LLM or screenshot call")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fail)
    monkeypatch.setattr(summarizer.screenshot, "capture", fail)

    with pytest.raises(SummarySkipped):
        await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
    assert art.summary_skipped_reason == "unreadable_pdf"


async def test_generate_summaries_prefers_a_pdf_feed_description_to_a_status(session, monkeypatch):
    # A document we couldn't read whose entry carries a real abstract: the
    # abstract is a better answer than "this PDF has no readable text".
    art = await _make_article(
        session, url="https://x/paper.pdf", full_text_fetched_at=datetime.now(UTC)
    )

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        return ("s", "m", "f")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    await generate_summaries(session, art)
    assert art.summary == "f"
    assert art.summary_skipped_reason is None


def test_unreadable_source_leaves_an_ordinary_thin_page_alone():
    # Only videos and documents get a source-specific status; a bot-blocked
    # HTML page still has a screenshot fallback worth reaching.
    art = Article(url="https://x/a", full_text="", full_text_fetched_at=datetime.now(UTC))
    assert summarizer.unreadable_source(art, "") is None
    # And nothing is stamped before the fetch has actually been attempted.
    unfetched = Article(url="https://x/paper.pdf", full_text="", full_text_fetched_at=None)
    assert summarizer.unreadable_source(unfetched, "") is None


async def test_generate_summaries_skips_real_short_source_without_llm_or_vision(
    session, monkeypatch
):
    # No stored summary: a genuinely short post gets the terminal stamp.
    # (A short source *under* a stored summary is rot, not a short post —
    # see test_generate_summaries_short_refetch_keeps_an_existing_summary.)
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "Seed7 is a GPL-licensed open source programming language."

    async def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("short post attempted an LLM or screenshot call")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fail)
    monkeypatch.setattr(summarizer.screenshot, "capture", fail)

    with pytest.raises(SummarySkipped):
        await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
    assert art.summary_short == ""
    assert art.summary_medium == ""
    assert art.summary == ""
    assert art.summary_model is None
    assert art.summary_generated_at is None
    assert art.summary_skipped_reason == "too_short"


async def test_generate_summaries_thin_raises(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "You need to enable JavaScript to run this app."

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

    return llm.LLMConfig(
        provider="openai",
        api_key="sk-x",
        base_url=None,
        model="gpt-5",
        user_owned=True,
        supports_vision=supports_vision,
    )


async def test_thin_with_vision_summarizes_from_screenshot(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "You need to enable JavaScript to run this app."

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
        return "You need to enable JavaScript to run this app."

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
        return "You need to enable JavaScript to run this app."

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
        return "You need to enable JavaScript to run this app."

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
        return "You need to enable JavaScript to run this app."

    async def fake_capture(url):
        return None

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    with pytest.raises(ThinContentError):
        await generate_summaries(session, art, config=_vision_config(), allow_vision=True)


# --- unusable pages (the model refused to summarize a 404/paywall/bot page) ---


async def test_generate_summaries_unusable_page_is_recorded_not_raised(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        raise summarizer.llm.UnusableContentError("404 page not found")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    # Returns normally: the LLM call succeeded — the *page* is the problem —
    # so callers meter the spent tokens instead of recording an error.
    await generate_summaries(session, art)
    assert art.summary == ""
    assert art.summary_short == ""
    assert art.summary_model is None
    assert art.summary_generated_at is None
    assert art.summary_skipped_reason == "unusable_page"


async def test_generate_summaries_unusable_page_keeps_an_existing_summary(session, monkeypatch):
    # A force-regenerate of an article whose page rotted away since: the
    # stored summary is the only good copy left and must survive the attempt.
    art = await _make_article(session)
    art.summary = "old full"
    art.summary_short = "old short"
    art.summary_medium = "old medium"
    art.summary_model = "old-model"
    await session.commit()

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_summarize(title, text, **kwargs):
        raise summarizer.llm.UnusableContentError("404 page not found")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fake_summarize)

    await generate_summaries(session, art)
    assert art.summary == "old full"
    assert art.summary_short == "old short"
    assert art.summary_model == "old-model"
    # Stamped so worker-eligible legacy rows (summary but no summary_short)
    # don't re-attempt — and burn an LLM call — every cycle.
    assert art.summary_skipped_reason == "unusable_page"


async def test_generate_summaries_short_refetch_keeps_an_existing_summary(session, monkeypatch):
    # The too_short leg of the same rule: a regenerate whose refetch comes
    # back a stub (the page died) must not trade a stored summary for a
    # terminal "too_short" stamp.
    art = await _make_article(session)
    art.summary = "old full"
    art.summary_short = "old short"
    await session.commit()

    async def fake_ensure(session_, article, allow_refetch=True):
        return "Page not found. Check the URL or head back to the homepage."

    async def fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("stub page attempted an LLM call")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize", fail)

    with pytest.raises(SummarySkipped):
        await generate_summaries(session, art)
    assert art.summary == "old full"
    assert art.summary_short == "old short"
    assert art.summary_skipped_reason == "unusable_page"


async def test_screenshot_unusable_page_is_recorded(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "You need to enable JavaScript to run this app."

    async def fake_capture(url):
        return b"jpeg"

    async def fake_summarize_screenshot(title, shot, **kwargs):
        raise summarizer.llm.UnusableContentError("404 error screen")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    monkeypatch.setattr(summarizer.llm, "summarize_screenshot", fake_summarize_screenshot)

    await generate_summaries(session, art, config=_vision_config(), allow_vision=True)
    assert art.summary == ""
    assert art.summary_skipped_reason == "unusable_page"


# --- stream_summaries (the SSE path must persist exactly like the batch one) ---


async def _collect(stream):
    return [event async for event in stream]


async def test_stream_summaries_streams_and_persists(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_stream(title, text, **kwargs):
        yield {"type": "delta", "text": "full "}
        yield {"type": "delta", "text": "three"}
        yield {"type": "result", "levels": ("short one", "medium two", "full three")}

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize_stream", fake_stream)
    monkeypatch.setattr(summarizer.settings, "openai_model", "test-model")

    events = await _collect(summarizer.stream_summaries(session, art))
    assert [e["type"] for e in events] == ["status", "status", "delta", "delta", "done"]
    assert [e["stage"] for e in events[:2]] == ["reading", "summarizing"]
    assert art.summary == "full three"
    assert art.summary_short == "short one"
    assert art.summary_medium == "medium two"
    assert art.summary_model == "test-model"
    assert art.summary_generated_at is not None
    assert art.summary_skipped_reason is None


async def test_stream_summaries_too_short_yields_skipped(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "Seed7 is a GPL-licensed open source programming language."

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)

    events = await _collect(summarizer.stream_summaries(session, art))
    assert events[-1] == {"type": "skipped", "reason": "too_short"}
    assert art.summary_skipped_reason == "too_short"


async def test_stream_summaries_yields_the_source_specific_skip(session, monkeypatch):
    art = await _make_article(
        session, url="https://x/paper.pdf", full_text_fetched_at=datetime.now(UTC)
    )

    async def fake_ensure(session_, article, allow_refetch=True):
        return ""

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)

    events = await _collect(summarizer.stream_summaries(session, art))
    assert events[-1] == {"type": "skipped", "reason": "unreadable_pdf"}
    assert art.summary_skipped_reason == "unreadable_pdf"


async def test_stream_summaries_unusable_yields_skipped(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_stream(title, text, **kwargs):
        raise summarizer.llm.UnusableContentError("404 page")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize_stream", fake_stream)

    events = await _collect(summarizer.stream_summaries(session, art))
    assert events[-1] == {"type": "skipped", "reason": "unusable_page"}
    assert art.summary_skipped_reason == "unusable_page"


async def test_stream_summaries_unusable_keeps_an_existing_summary(session, monkeypatch):
    art = await _make_article(session)
    art.summary = "old full"
    art.summary_short = "old short"
    await session.commit()

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_stream(title, text, **kwargs):
        raise summarizer.llm.UnusableContentError("404 page")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize_stream", fake_stream)

    events = await _collect(summarizer.stream_summaries(session, art))
    assert events[-1] == {"type": "error", "detail": summarizer.SUMMARY_KEPT_DETAIL}
    assert art.summary == "old full"
    assert art.summary_skipped_reason == "unusable_page"


async def test_stream_summaries_short_refetch_keeps_an_existing_summary(session, monkeypatch):
    art = await _make_article(session)
    art.summary = "old full"
    art.summary_short = "old short"
    await session.commit()

    async def fake_ensure(session_, article, allow_refetch=True):
        return "Page not found. Check the URL or head back to the homepage."

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)

    events = await _collect(summarizer.stream_summaries(session, art))
    assert events[-1] == {"type": "error", "detail": summarizer.SUMMARY_KEPT_DETAIL}
    assert art.summary == "old full"
    assert art.summary_skipped_reason == "unusable_page"


async def test_stream_summaries_thin_page_streams_the_vision_answer_whole(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "You need to enable JavaScript to run this app."

    async def fake_capture(url):
        return b"jpeg"

    async def fake_summarize_screenshot(title, shot, **kwargs):
        return ("short", "medium", "full from image")

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.screenshot, "capture", fake_capture)
    monkeypatch.setattr(summarizer.llm, "summarize_screenshot", fake_summarize_screenshot)

    events = await _collect(summarizer.stream_summaries(session, art, config=_vision_config()))
    assert [e["type"] for e in events] == ["status", "status", "delta", "done"]
    assert events[1]["stage"] == "rendering"
    assert events[2]["text"] == "full from image"
    assert art.summary == "full from image"


async def test_stream_summaries_refuses_a_video_whose_captions_are_still_owed(session, monkeypatch):
    art = await _make_article(session, url="https://www.youtube.com/watch?v=RsR6cbovMfI")

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    with pytest.raises(ThinContentError, match="throttling caption requests"):
        await _collect(summarizer.stream_summaries(session, art))


async def test_stream_summaries_empty_summary_raises(session, monkeypatch):
    art = await _make_article(session)

    async def fake_ensure(session_, article, allow_refetch=True):
        return "x" * 500

    async def fake_stream(title, text, **kwargs):
        yield {"type": "result", "levels": ("", "", "")}

    monkeypatch.setattr(summarizer, "ensure_full_text", fake_ensure)
    monkeypatch.setattr(summarizer.llm, "summarize_stream", fake_stream)
    with pytest.raises(RuntimeError):
        await _collect(summarizer.stream_summaries(session, art))
