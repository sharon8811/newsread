import types
from datetime import UTC, datetime, timedelta

from app import extractor
from app.extractor import (
    _recently_attempted,
    clip_for_llm,
    enrich_article,
    ensure_full_text,
    fetch_page,
    is_thin,
    is_too_short_to_summarize,
    is_visual_stub,
)
from app.models import Article, Feed


def test_is_thin():
    assert is_thin("short")
    assert not is_thin("x" * 400)


def test_short_source_classification_preserves_visual_fallbacks():
    assert is_too_short_to_summarize("Seed7 is a GPL-licensed language.")
    assert not is_too_short_to_summarize("x" * 400)
    assert is_visual_stub("")
    assert is_visual_stub("You need to enable JavaScript to run this app.")
    assert is_visual_stub("  Checking   your browser before accessing the site ")
    assert not is_visual_stub("A concise but meaningful post.")


def test_clip_for_llm():
    assert clip_for_llm("short") == "short"
    long = "x" * (extractor.MAX_LLM_CHARS + 10)
    clipped = clip_for_llm(long)
    assert clipped.endswith("[article truncated]")


def _fake_page(status=200, html="<html></html>", css_result=None):
    return types.SimpleNamespace(
        status=status,
        html_content=html,
        css=lambda selector: css_result or [],
    )


async def test_fetch_page_success(monkeypatch):
    page = _fake_page(html="<html><body>content</body></html>")

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "extracted prose")
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract_metadata",
        lambda html: types.SimpleNamespace(image="https://x/og.png"),
    )
    text, image = await fetch_page("https://x/a")
    assert text == "extracted prose"
    assert image == "https://x/og.png"


async def test_fetch_page_fetch_raises(monkeypatch):
    async def fake_get(url, **kwargs):
        raise RuntimeError("blocked")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    assert await fetch_page("https://x/a") == ("", None)


async def test_fetch_page_non_200(monkeypatch):
    async def fake_get(url, **kwargs):
        return _fake_page(status=403)

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    assert await fetch_page("https://x/a") == ("", None)


async def test_fetch_page_image_from_css_fallback(monkeypatch):
    page = _fake_page(css_result=["https://x/twitter.png"])

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "text")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, image = await fetch_page("https://x/a")
    assert image == "https://x/twitter.png"


async def test_fetch_page_rejects_relative_image(monkeypatch):
    page = _fake_page(css_result=["/relative/path.png"])

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "text")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, image = await fetch_page("https://x/a")
    assert image is None


async def test_fetch_page_metadata_raises_but_survives(monkeypatch):
    page = _fake_page()

    async def fake_get(url, **kwargs):
        return page

    def boom(html):
        raise ValueError("bad meta")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "text")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", boom)
    text, image = await fetch_page("https://x/a")
    assert text == "text"
    assert image is None


def _recent(seconds):
    return datetime.now(UTC) - timedelta(seconds=seconds)


def test_recently_attempted():
    art = Article(full_text_fetched_at=None)
    assert not _recently_attempted(art)
    art.full_text_fetched_at = _recent(60)
    assert _recently_attempted(art)
    art.full_text_fetched_at = _recent(60 * 60 * 24)  # a day ago
    assert not _recently_attempted(art)


async def _make_article(session, **kwargs):
    feed = Feed(url=f"https://feed/{kwargs.get('guid', 'x')}")
    session.add(feed)
    await session.flush()
    art = Article(
        feed_id=feed.id,
        guid=kwargs.get("guid", "g"),
        url="https://x/a",
        title="T",
        content_html=kwargs.get("content_html", ""),
        full_text=kwargs.get("full_text", ""),
        image_url=kwargs.get("image_url"),
    )
    session.add(art)
    await session.commit()
    await session.refresh(art)
    return art


async def test_enrich_article_fills_text_and_image(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")

    async def fake_fetch_page(url):
        return "the full extracted text", "https://x/og.png"

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.full_text == "the full extracted text"
    assert art.image_url == "https://x/og.png"
    assert art.full_text_fetched_at is not None


async def test_enrich_article_skips_when_nothing_needed(session, monkeypatch):
    art = await _make_article(session, full_text="already have text", image_url="https://x/i.png")
    called = False

    async def fake_fetch_page(url):
        nonlocal called
        called = True
        return "x", "y"

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert not called
    assert art.full_text_fetched_at is not None


async def test_enrich_article_stamps_rich_body_with_image(session, monkeypatch):
    # Regression: the worker batch query and feeds pending_count select on
    # full_text == '' OR image_url IS NULL with a NULL stamp. A rich feed body
    # (need_text false) with an image already set fetches nothing — but it must
    # still be stamped, or it stays "enriching…" forever.
    rich = "<p>" + ("word " * 200) + "</p>"
    art = await _make_article(session, content_html=rich, image_url="https://x/i.png")

    async def fake_fetch_page(url):
        raise AssertionError("should not fetch")

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.full_text == ""
    assert art.full_text_fetched_at is not None


async def test_enrich_article_stamps_when_no_image_found(session, monkeypatch):
    # Regression: rich body, missing image, page yields no image — the attempt
    # must be stamped so the article is not re-selected (and re-fetched) forever.
    rich = "<p>" + ("word " * 200) + "</p>"
    art = await _make_article(session, content_html=rich)

    async def fake_fetch_page(url):
        return "", None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.image_url is None
    assert art.full_text == ""
    assert art.full_text_fetched_at is not None


async def test_ensure_full_text_returns_existing(session):
    art = await _make_article(session, full_text="existing full text")
    assert await ensure_full_text(session, art) == "existing full text"


async def test_ensure_full_text_uses_long_content_fallback(session):
    long_html = "<p>" + ("word " * 300) + "</p>"
    art = await _make_article(session, content_html=long_html)
    out = await ensure_full_text(session, art)
    assert len(out) >= extractor.MIN_USEFUL_CHARS


async def test_ensure_full_text_fetches_when_thin(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")

    async def fake_fetch_page(url):
        return "freshly fetched body text", None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    out = await ensure_full_text(session, art)
    assert out == "freshly fetched body text"


async def test_ensure_full_text_no_refetch_when_recent(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")
    art.full_text_fetched_at = datetime.now(UTC)
    await session.commit()

    async def fake_fetch_page(url):
        raise AssertionError("should not fetch")

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    out = await ensure_full_text(session, art, allow_refetch=False)
    assert out == "thin"  # thin content fallback, no refetch
