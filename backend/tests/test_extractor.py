import types
from datetime import UTC, datetime, timedelta

import pytest

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


def _fake_page(status=200, html="<html></html>", css_result=None, body=None, headers=None):
    return types.SimpleNamespace(
        status=status,
        html_content=html,
        body=body,
        headers=headers or {},
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
        lambda html: types.SimpleNamespace(image="https://x/og.png", title="Page Title"),
    )
    text, image, title = await fetch_page("https://x/a")
    assert text == "extracted prose"
    assert image == "https://x/og.png"
    assert title == "Page Title"


async def test_fetch_page_fetch_raises(monkeypatch):
    async def fake_get(url, **kwargs):
        raise RuntimeError("blocked")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    assert await fetch_page("https://x/a") == ("", None, None)


async def test_fetch_page_non_200(monkeypatch):
    async def fake_get(url, **kwargs):
        return _fake_page(status=403)

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    assert await fetch_page("https://x/a") == ("", None, None)


async def test_fetch_page_reads_a_pdf_instead_of_its_bytes(monkeypatch):
    # The regression this exists for: trafilatura happily "extracts" a PDF's
    # operators, and 24k characters of %PDF-1.7 /FlateDecode used to reach the
    # model, which answered that it had been handed a binary.
    page = _fake_page(body=b"%PDF-1.7 ...", headers={"Content-Type": "application/pdf"})

    async def fake_get(url, **kwargs):
        return page

    async def fake_extract(body):
        assert body == b"%PDF-1.7 ..."
        return "the paper's prose", "A Paper"

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.pdf, "extract_text", fake_extract)
    monkeypatch.setattr(
        extractor.trafilatura, "extract", lambda *a, **k: pytest.fail("PDFs are not HTML")
    )
    # No lead image: og:image never applies to a document.
    assert await fetch_page("https://x/paper.pdf") == ("the paper's prose", None, "A Paper")


async def test_fetch_page_recognizes_a_pdf_with_no_suffix_or_content_type(monkeypatch):
    # arxiv serves /pdf/1706.03762 with no extension; the signature decides.
    page = _fake_page(body=b"%PDF-1.4 ...", headers={"content-type": "application/octet-stream"})

    async def fake_get(url, **kwargs):
        return page

    async def fake_extract(body):
        return "prose", None

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.pdf, "extract_text", fake_extract)
    text, image, title = await fetch_page("https://arxiv.org/pdf/1706.03762")
    assert (text, image, title) == ("prose", None, None)


async def test_fetch_page_ignores_a_body_it_cannot_read_as_bytes(monkeypatch):
    # Some fetcher backends hand back only decoded text; re-encoding a lossily
    # decoded PDF would corrupt its signature, so those stay on the HTML path.
    page = _fake_page(body="%PDF-1.7 as a string", html="<html><body>x</body></html>")

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "html prose")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, _, _ = await fetch_page("https://x/a")
    assert text == "html prose"


async def test_fetch_page_image_from_css_fallback(monkeypatch):
    page = _fake_page(css_result=["https://x/twitter.png"])

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "text")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, image, title = await fetch_page("https://x/a")
    assert image == "https://x/twitter.png"
    assert title is None


async def test_fetch_page_rejects_relative_image(monkeypatch):
    page = _fake_page(css_result=["/relative/path.png"])

    async def fake_get(url, **kwargs):
        return page

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "text")
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, image, title = await fetch_page("https://x/a")
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
    text, image, title = await fetch_page("https://x/a")
    assert text == "text"
    assert image is None
    assert title is None


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
        url=kwargs.get("url", "https://x/a"),
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
        return "the full extracted text", "https://x/og.png", "T2"

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
        return "x", "y", "z"

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert not called
    assert art.full_text_fetched_at is not None


async def test_enrich_video_summarizes_from_captions_not_the_watch_page(session, monkeypatch):
    # A video description usually clears the is_thin bar, so the transcript —
    # the only real source — must be fetched regardless of its length.
    long_description = "<p>" + ("word " * 200) + "</p>"
    art = await _make_article(
        session,
        url="https://www.youtube.com/watch?v=RsR6cbovMfI",
        content_html=long_description,
    )

    async def fake_fetch_page(url):
        raise AssertionError("videos must not be scraped")

    async def fake_transcript(video):
        assert video == "RsR6cbovMfI"
        return "spoken words from the video"

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(extractor.youtube, "fetch_transcript", fake_transcript)
    await enrich_article(session, art)
    assert art.full_text == "spoken words from the video"
    assert art.image_url == "https://i.ytimg.com/vi/RsR6cbovMfI/hqdefault.jpg"
    assert art.full_text_fetched_at is not None


async def test_enrich_video_keeps_the_feed_thumbnail_and_stamps_without_captions(
    session, monkeypatch
):
    art = await _make_article(
        session,
        url="https://www.youtube.com/watch?v=RsR6cbovMfI",
        image_url="https://i3.ytimg.com/vi/RsR6cbovMfI/hqdefault.jpg",
    )

    async def no_captions(video):
        return ""

    monkeypatch.setattr(extractor.youtube, "fetch_transcript", no_captions)
    await enrich_article(session, art)
    assert art.full_text == ""
    assert art.image_url == "https://i3.ytimg.com/vi/RsR6cbovMfI/hqdefault.jpg"
    # Stamped anyway, or the feed's pending count never reaches zero.
    assert art.full_text_fetched_at is not None


async def test_enrich_video_stays_pending_when_youtube_blocks_us(session, monkeypatch):
    art = await _make_article(session, url="https://www.youtube.com/watch?v=RsR6cbovMfI")

    async def blocked(video):
        raise extractor.youtube.TranscriptBlocked("rate limited")

    monkeypatch.setattr(extractor.youtube, "fetch_transcript", blocked)
    await enrich_article(session, art)
    # Unstamped on purpose: the captions exist, YouTube just refused us, so a
    # later pass must be allowed to try again.
    assert art.full_text_fetched_at is None
    assert art.full_text == ""
    assert art.image_url == "https://i.ytimg.com/vi/RsR6cbovMfI/hqdefault.jpg"


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
        return "", None, None

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
        return "freshly fetched body text", None, None

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


async def test_enrich_article_stores_pdf_text_postgres_would_reject(session, monkeypatch):
    """End to end against the real database, because that is where this broke:
    a unit test on the extractor passes on a string Postgres refuses to store,
    and the failed UPDATE leaves the article unstamped and re-fetched forever."""
    art = await _make_article(session, url="https://cdn.openai.com/pdf/cdc_proof.pdf")

    async def fake_get(url, **kwargs):
        return _fake_page(body=b"%PDF-1.7 ...", headers={"Content-Type": "application/pdf"})

    class _Reader:
        is_encrypted = False
        metadata = None
        pages = [type("P", (), {"extract_text": lambda self: "the proof\x00 continues"})()]

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    await enrich_article(session, art)
    assert art.full_text == "the proof continues"
    assert art.full_text_fetched_at is not None


def _rendered_page(html="<html></html>", status=200):
    return _fake_page(status=status, html=html)


async def test_fetch_page_renders_a_page_that_refuses_a_plain_fetch(monkeypatch):
    # The regression this exists for: a growing share of sites answer a plain
    # request with a bot check and hand the same article to a browser that
    # runs their JavaScript. Fetch-once left those articles with no text at
    # all, and the summarizer then mistook a long essay for an image-only page.
    async def fake_get(url, **kwargs):
        return _fake_page(status=403, html="<html>Checking your browser</html>")

    async def fake_render(url):
        assert url == "https://blocked/a"
        return _rendered_page(html="<html><body>the real article</body></html>")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract",
        lambda html, **k: (
            "the article's prose" if "real article" in html else "Checking your browser"
        ),
    )
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract_metadata",
        lambda html: types.SimpleNamespace(image="https://x/og.png", title="Real Title"),
    )
    text, image, title = await fetch_page("https://blocked/a")
    assert text == "the article's prose"
    assert image == "https://x/og.png"
    assert title == "Real Title"


async def test_fetch_page_renders_a_page_that_returns_200_with_no_prose(monkeypatch):
    # Not every empty extraction is a block: a client-rendered page answers 200
    # with an empty shell. Same fallback, same recovery.
    async def fake_get(url, **kwargs):
        return _fake_page(html="<html><div id='root'></div></html>")

    async def fake_render(url):
        return _rendered_page(html="<html><body>hydrated</body></html>")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract",
        lambda html, **k: "hydrated prose" if "hydrated" in html else "",
    )
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, _, _ = await fetch_page("https://spa/a")
    assert text == "hydrated prose"


async def test_fetch_page_never_renders_a_pdf(monkeypatch):
    # A browser renders a PDF into a plugin viewer with no text in the DOM, so
    # the render must stay a fallback for the HTML path only — the plain fetch
    # is the one that reads documents.
    page = _fake_page(body=b"%PDF-1.7 ...", headers={"Content-Type": "application/pdf"})

    async def fake_get(url, **kwargs):
        return page

    async def fake_extract(body):
        return "the paper's prose", "A Paper"

    async def fake_render(url):
        raise AssertionError("PDFs must not be rendered")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor.pdf, "extract_text", fake_extract)
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    assert await fetch_page("https://x/paper.pdf") == ("the paper's prose", None, "A Paper")


async def test_fetch_page_keeps_the_plain_image_when_the_render_finds_no_prose(monkeypatch):
    async def fake_get(url, **kwargs):
        return _fake_page(status=403, html="<html>Just a moment</html>")

    async def fake_render(url):
        return _rendered_page(status=403, html="<html>Just a moment</html>")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    monkeypatch.setattr(extractor.trafilatura, "extract", lambda html, **k: "Just a moment")
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract_metadata",
        lambda html: types.SimpleNamespace(image="https://x/og.png", title=None),
    )
    text, image, _ = await fetch_page("https://blocked/a")
    # The bot check itself is not returned: a few hundred characters of "Just a
    # moment" reads to the summarizer as a real page that happens to be short,
    # and earns a terminal "too_short" stamp. Empty means "no source", which
    # routes to the screenshot fallback instead.
    assert text == ""
    assert image == "https://x/og.png"


async def test_fetch_page_survives_an_unavailable_renderer(monkeypatch):
    async def fake_get(url, **kwargs):
        return _fake_page(status=403)

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    # The autouse conftest fixture makes the browser unavailable; the caller
    # still gets a clean empty result rather than an exception.
    assert await fetch_page("https://x/a") == ("", None, None)


async def test_enrich_article_counts_a_fetch_that_yielded_nothing(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")

    async def fake_fetch_page(url):
        return "", None, None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.full_text_attempts == 1
    # Not written off yet: sites block intermittently and a later attempt often
    # reads the same page fine.
    assert art.summary_skipped_reason is None


async def test_enrich_article_does_not_count_a_fetch_that_worked(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")

    async def fake_fetch_page(url):
        return "the full text", None, None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.full_text_attempts == 0


async def test_enrich_article_gives_up_after_the_last_attempt(session, monkeypatch):
    # Without this stamp the article sits in limbo forever: no text, so the
    # summarize query skips it; no skip reason, so the clients show a summary
    # that is perpetually on the way.
    art = await _make_article(session, content_html="<p>thin</p>")
    art.full_text_attempts = extractor.MAX_TEXT_ATTEMPTS - 1
    await session.commit()

    async def fake_fetch_page(url):
        return "", None, None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.full_text_attempts == extractor.MAX_TEXT_ATTEMPTS
    assert art.summary_skipped_reason == "unusable_page"


async def test_enrich_article_keeps_going_when_the_feed_body_is_summarizable(session, monkeypatch):
    # A rich feed body is enough to summarize from, so a page we cannot read
    # is not a dead end and must not be stamped unusable.
    body = "<p>" + ("word " * 900) + "</p>"
    art = await _make_article(session, content_html=body, image_url="https://x/i.png")
    art.full_text_attempts = extractor.MAX_TEXT_ATTEMPTS
    await session.commit()

    async def fake_fetch_page(url):
        raise AssertionError("a rich feed body needs no page fetch")

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.summary_skipped_reason is None


async def test_enrich_article_leaves_an_existing_summary_alone(session, monkeypatch):
    art = await _make_article(session, content_html="<p>thin</p>")
    art.full_text_attempts = extractor.MAX_TEXT_ATTEMPTS - 1
    art.summary = "a summary generated from a screenshot"
    await session.commit()

    async def fake_fetch_page(url):
        return "", None, None

    monkeypatch.setattr(extractor, "fetch_page", fake_fetch_page)
    await enrich_article(session, art)
    assert art.summary_skipped_reason is None
    assert art.summary == "a summary generated from a screenshot"


async def test_render_page_runs_the_challenge_wait_and_returns_the_page(monkeypatch):
    import scrapling.fetchers

    waited: list[int] = []
    page = _rendered_page(html="<html>rendered</html>")

    class FakePage:
        async def wait_for_timeout(self, ms):
            waited.append(ms)

    async def fake_async_fetch(url, **kwargs):
        # The wait is the whole point: an interstitial only clears after its
        # own JavaScript has run.
        assert await kwargs["page_action"](FakePage()) is not None
        assert "--no-sandbox" in kwargs["extra_flags"]
        return page

    monkeypatch.setattr(
        scrapling.fetchers.DynamicFetcher, "async_fetch", staticmethod(fake_async_fetch)
    )
    assert await extractor._render_page("https://x/a") is page
    assert waited == [extractor.RENDER_CHALLENGE_WAIT_MS]


async def test_render_page_returns_none_when_the_browser_fails(monkeypatch):
    import scrapling.fetchers

    async def boom(url, **kwargs):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(scrapling.fetchers.DynamicFetcher, "async_fetch", staticmethod(boom))
    assert await extractor._render_page("https://x/a") is None


async def test_fetch_page_never_returns_a_refusal_as_the_article(monkeypatch):
    # Regression from the live run: Cloudflare's interstitial extracts to
    # "current.org Performing security verification ..." — the host name comes
    # first, so a prefix test misses it, and 326 characters of boilerplate
    # looked like a short article. The response status settles it instead.
    async def fake_get(url, **kwargs):
        return _fake_page(status=403, html="<html>verification</html>")

    async def fake_render(url):
        return _rendered_page(status=403, html="<html>verification</html>")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    monkeypatch.setattr(
        extractor.trafilatura,
        "extract",
        lambda html, **k: (
            "current.org Performing security verification This website uses a security "
            "service to protect itself from online attacks."
        ),
    )
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    assert await fetch_page("https://current.org/a") == ("", None, None)


async def test_fetch_page_keeps_a_genuinely_short_200_page(monkeypatch):
    # The status gate must not swallow real content: a short post served with a
    # 200 is the article, and summarizing (or skipping) it is the caller's call.
    async def fake_get(url, **kwargs):
        return _fake_page(html="<html>short</html>")

    async def fake_render(url):
        raise AssertionError("a 200 with prose needs no render")

    monkeypatch.setattr(extractor.AsyncFetcher, "get", staticmethod(fake_get))
    monkeypatch.setattr(extractor, "_render_page", fake_render)
    monkeypatch.setattr(
        extractor.trafilatura, "extract", lambda html, **k: "Seed7 is a GPL-licensed language."
    )
    monkeypatch.setattr(extractor.trafilatura, "extract_metadata", lambda html: None)
    text, _, _ = await fetch_page("https://x/short")
    assert text == "Seed7 is a GPL-licensed language."
