"""Original-source enrichment: Scrapling fetches once, we take prose + og:image.

"Source" rather than "page" because a feed item's link is not always an
article. A YouTube link is a video whose prose lives in its captions, and a
link ending in .pdf is a document — both are recognized from the URL or the
fetched bytes and routed away from the HTML path, which would otherwise hand
the summarizer a watch-page footer or a stream of PDF operators.

"Fetches once" is the common case, not the rule: a plain HTTP fetch is cheap
and works for most of the web, but a growing share of sites answer it with a
bot check ("Checking your browser...") and only serve the article to something
that runs their JavaScript. When the cheap fetch comes back with no prose, we
spend a real browser render on the page before giving up.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import trafilatura
from scrapling.fetchers import AsyncFetcher
from sqlalchemy.ext.asyncio import AsyncSession

from . import pdf, processing_events, youtube
from .fetcher import strip_html
from .models import Article

logger = logging.getLogger(__name__)

# Feed content longer than this is treated as the real article body;
# shorter content (e.g. hnrss link stubs) triggers a fetch of the original page.
MIN_USEFUL_CHARS = 800

MAX_LLM_CHARS = 24_000

# A failed page fetch leaves only the feed's own content_html to summarize
# from. Below this it's a stub the summarizer would skip every cycle, so the
# batch worker never picks the article up — and the progress indicators must
# agree, or they'd wait on work that will never happen.
SUMMARIZABLE_FEED_HTML_CHARS = 1600

# Short pages that contain only a browser/app shell are not "already short";
# their useful content may be visual and can still be grounded by a screenshot.
# Keep these deliberately narrow so ordinary short posts never spend a vision
# call merely to restate themselves.
_VISUAL_STUB_PREFIXES = (
    "you need to enable javascript to run this app",
    "enable javascript and cookies to continue",
    "javascript is disabled",
    "just a moment",
    "checking your browser",
    "please verify you are human",
)

# The same interstitials, matched anywhere in a short extraction rather than
# only at its start. Cloudflare's page extracts as "current.org Performing
# security verification ..." — the host name comes first, so a prefix test
# misses the very thing it was added for. Bounded by length because a real
# article may well mention a bot check in passing; a page that says this and
# little else is one.
_VISUAL_STUB_MARKERS = (
    "performing security verification",
    "checking your browser",
    "just a moment",
    "please verify you are human",
    "enable javascript and cookies to continue",
    "attention required",
    # Cloudflare's JS challenge, verbatim from reuters.com's 401 body. Served
    # with a 200 elsewhere, where the status gate would not catch it.
    "please enable js and disable any ad blocker",
)
_STUB_SCAN_CHARS = 1_000

# Don't re-hit a page that recently failed to yield text (site likely blocks bots).
REFETCH_COOLDOWN = timedelta(hours=6)

# How long to let a page's own JavaScript run before reading it. Interstitials
# ("Checking your browser...") clear in a few seconds; measured against the
# real ones, 12s clears the sites that clear at all and the ones that don't
# are still refusing after 35s, so waiting longer only costs a browser slot.
RENDER_CHALLENGE_WAIT_MS = 12_000
RENDER_TIMEOUT_MS = 60_000

# Each render is a Chromium process, so this is a memory bound as much as a
# concurrency one. Module-level for the same reason as the worker's stage
# gates: it has to hold across overlapping jobs, not per call.
RENDER_CONCURRENCY = 2
_RENDER_GATE = asyncio.Semaphore(RENDER_CONCURRENCY)

# How many times we fetch a page that yields nothing before calling it
# unreadable. Sites block intermittently — roughly one stuck article in four
# turned out to be a page that simply answered a later request — so a single
# refusal is not proof, and a fourth attempt has never been the one that works.
MAX_TEXT_ATTEMPTS = 3


def _response_body(page) -> bytes | None:
    """The raw bytes behind a fetched response, or None if this backend has
    only decoded them. `html_content` is useless for a binary — re-encoding a
    lossily-decoded PDF corrupts its signature along with everything else."""
    body = getattr(page, "body", None)
    return body if isinstance(body, bytes) else None


def _header(page, name: str) -> str | None:
    """A response header, looked up case-insensitively: the fetcher backends
    disagree on whether their header mapping folds case for us."""
    for key, value in (getattr(page, "headers", None) or {}).items():
        if key.lower() == name:
            return str(value)
    return None


def _read_html(page) -> tuple[str, str | None, str | None]:
    """Prose, lead image and title from a fetched HTML page."""
    html = page.html_content
    text = trafilatura.extract(html, include_comments=False) or ""

    image: str | None = None
    title: str | None = None
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.image:
            image = meta.image
        if meta and meta.title:
            title = meta.title
    except Exception:
        pass
    if not image:
        for selector in (
            'meta[property="og:image"]::attr(content)',
            'meta[name="twitter:image"]::attr(content)',
        ):
            for found in page.css(selector):
                value = str(found).strip()
                if value:
                    image = value
                    break
            if image:
                break
    if image and not image.startswith(("http://", "https://")):
        image = None
    return text, image, title


def _is_prose(page, text: str) -> bool:
    """True when an extraction is the article rather than a bot check.

    Status first: a 401/402/403 body is a refusal, never the article, and its
    extraction is at best the site's own explanation of why we can't have it.
    Letting that through is worse than returning nothing, because a few
    hundred characters of "Performing security verification" reads to the
    summarizer as a real page that happens to be short — and gets stamped
    "too_short", which is terminal and blocks even a manual retry.

    Then the body: an interstitial served with a 200 extracts to a sentence or
    two of "Checking your browser...", which is no more summarizable and, like
    the refusals, is worth re-fetching with something that can clear it.
    """
    return page.status == 200 and bool(text.strip()) and not is_visual_stub(text)


async def _render_page(url: str):
    """Fetch a page through a real browser, giving its JavaScript time to run.

    Returns the fetched page, or None when rendering is unavailable (a local
    run without browsers) or failed. Deliberately separate from
    screenshot.capture: that one wants pixels and this one wants the DOM, and
    a page that renders is not always a page that photographs.
    """
    try:
        from scrapling.fetchers import DynamicFetcher
    except Exception as exc:  # pragma: no cover - import guard for local runs
        logger.warning("Browser rendering unavailable: %s", exc)
        return None

    async def wait_out_the_challenge(page):
        await page.wait_for_timeout(RENDER_CHALLENGE_WAIT_MS)
        return page

    try:
        async with _RENDER_GATE:
            return await DynamicFetcher.async_fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=RENDER_TIMEOUT_MS,
                page_action=wait_out_the_challenge,
                # The backend container runs as root, where Chromium refuses
                # to start its sandbox; the container is the isolation boundary.
                extra_flags=["--no-sandbox"],
            )
    except Exception as exc:
        logger.warning("Rendering %s failed: %s", url, exc)
        return None


async def fetch_page(url: str) -> tuple[str, str | None, str | None]:
    """Fetch a source; return (extracted prose, lead image URL, title).

    Handles both an HTML page and a PDF document: the branch is taken on what
    came back, not on what the URL looked like, so a paper served without a
    .pdf suffix is read as a paper.

    Two fetches, not one, when the first comes up empty: sites that answer a
    plain request with a 403 or a bot check hand the same article over to a
    browser that runs their JavaScript. The render is strictly a fallback —
    never a replacement — because the cheap path is the one that reads PDFs,
    which a browser renders into a plugin viewer with no text in the DOM.
    """
    page = None
    try:
        page = await AsyncFetcher.get(url, impersonate="chrome")
    except Exception as exc:
        logger.warning("Page fetch of %s failed: %s", url, exc)
    if page is not None and page.status == 200:
        body = _response_body(page)
        if pdf.is_pdf(body, _header(page, "content-type")):
            # No lead image: rendering page one to a thumbnail is a different
            # feature, and og:image never applies to a document.
            text, title = await pdf.extract_text(body or b"")
            logger.info("Read %d characters of text from the PDF at %s", len(text), url)
            return text, None, title
    elif page is not None:
        logger.warning("Page fetch of %s returned %s", url, page.status)

    text, image, title = _read_html(page) if page is not None else ("", None, None)
    if page is not None and _is_prose(page, text):
        return text, image, title

    logger.info("Plain fetch of %s yielded no prose; rendering it in a browser", url)
    rendered = await _render_page(url)
    if rendered is not None:
        rendered_text, rendered_image, rendered_title = _read_html(rendered)
        if _is_prose(rendered, rendered_text):
            logger.info("Rendering %s recovered %d characters of prose", url, len(rendered_text))
            return rendered_text, rendered_image or image, rendered_title or title
        logger.info("Rendering %s still yields no prose (status %s)", url, rendered.status)
        image = image or rendered_image
        title = title or rendered_title
    # Empty text, not the refusal we were handed: callers read "" as "no
    # source" (is_visual_stub) and route to the screenshot fallback or the
    # give-up stamp, both of which beat summarizing a bot check. The image and
    # title still stand — an og:image survives a page whose prose does not.
    return "", image, title


async def _enrich_video(article: Article, video: str) -> bool:
    """A video's prose is its captions; its watch page has none to extract.

    Unconditional, unlike the page path below: a video description often runs
    past the is_thin threshold, which would otherwise leave the transcript —
    the only real source — unfetched.

    Returns whether the article is done (False keeps it pending: YouTube
    refused this request and the captions are still out there).
    """
    done = True
    if not article.full_text:
        try:
            article.full_text = await youtube.fetch_transcript(video)
        except youtube.TranscriptBlocked as exc:
            logger.info("Transcript for %s deferred: %s", video, exc)
            done = False
    if not article.image_url:
        # Feed entries carry a media:thumbnail; this covers the ones that don't.
        article.image_url = f"https://i.ytimg.com/vi/{video}/hqdefault.jpg"
    return done


def _has_no_usable_text(article: Article) -> bool:
    """True when neither the page nor the feed gave us anything to summarize.

    Measured on visible text, not on markup: the same `is_thin(strip_html(...))`
    test that decided to fetch the page in the first place. Raw HTML length
    would disagree with that decision for a markup-heavy entry carrying only a
    line or two of prose — fetched because its text is thin, then never
    written off because its markup is long.
    """
    return not article.full_text and is_thin(strip_html(article.content_html))


async def enrich_article(session: AsyncSession, article: Article) -> None:
    """Fill full_text and image_url from the original source.

    The video branch is taken here, before any fetch, because a watch page
    has nothing to extract. PDFs need no branch of their own: one fetch
    serves both, and fetch_page reads whichever came back.

    A fetch that yields no prose is counted, not treated as final — sites
    block intermittently, and the article is re-attempted (after
    REFETCH_COOLDOWN, up to MAX_TEXT_ATTEMPTS) before being written off as
    unreadable.
    """
    if video := youtube.video_id(article.url):
        # A blocked request leaves the stamp NULL so a later pass retries it —
        # the only case where an article stays pending on purpose.
        if await _enrich_video(article, video):
            article.full_text_fetched_at = datetime.now(UTC)
        await session.commit()
        return

    need_text = not article.full_text and is_thin(strip_html(article.content_html))
    need_image = not article.image_url
    if need_text or need_image:
        text, image, _ = await fetch_page(article.url)
        if need_text:
            article.full_text = text
            if not text:
                article.full_text_attempts += 1
        if need_image and image:
            article.image_url = image[:2048]
    # Stamp unconditionally: the worker's batch query and the feeds
    # pending_count treat a NULL stamp as "still pending", so an article that
    # needs nothing (rich feed body, image already set) or whose page yields
    # no image would otherwise stay pending — and be re-fetched — forever.
    article.full_text_fetched_at = datetime.now(UTC)
    if article.full_text_attempts >= MAX_TEXT_ATTEMPTS and _has_no_usable_text(article):
        _give_up_on_the_page(session, article)
    await session.commit()


def _give_up_on_the_page(session: AsyncSession, article: Article) -> None:
    """Record that the original page will not give up its text.

    Without this an article whose site blocks us sits in limbo forever: no
    text, so the summarize query skips it; no skip reason, so the clients show
    a summary that is perpetually "on the way" and the feed's pending count
    never settles. "unusable_page" is the reason that already means exactly
    this — the page is not the article (404, paywall, bot check) — and the
    clients already have copy for it. A force-regenerate still retries.
    """
    if article.summary or article.summary_skipped_reason is not None:
        return
    article.summary_skipped_reason = "unusable_page"
    processing_events.add_event(
        session,
        stage=processing_events.STAGE_SUMMARIZE,
        outcome=processing_events.OUTCOME_SKIPPED,
        article_id=article.id,
        feed_id=article.feed_id,
        detail="unusable_page",
    )
    logger.info(
        "Article %s: %d fetches of %s yielded no text; marking it unusable",
        article.id,
        article.full_text_attempts,
        article.url,
    )


def _recently_attempted(article: Article) -> bool:
    if article.full_text_fetched_at is None:
        return False
    return datetime.now(UTC) - article.full_text_fetched_at < REFETCH_COOLDOWN


async def ensure_full_text(
    session: AsyncSession, article: Article, allow_refetch: bool = True
) -> str:
    """Return the best available article text, fetching and caching it if needed."""
    if article.full_text:
        return article.full_text

    fallback = strip_html(article.content_html)
    if len(fallback) >= MIN_USEFUL_CHARS:
        return fallback

    if not allow_refetch and _recently_attempted(article):
        return fallback

    await enrich_article(session, article)
    return article.full_text or fallback


def clip_for_llm(text: str) -> str:
    if len(text) <= MAX_LLM_CHARS:
        return text
    return text[:MAX_LLM_CHARS] + "\n\n[article truncated]"


def is_thin(text: str) -> bool:
    """True when all we have is a link stub — too little to ground an LLM on."""
    return len(text.strip()) < 400


def is_visual_stub(text: str) -> bool:
    """True when short extracted text is an empty/browser shell.

    These pages may still be useful as screenshots (maps, comics, charts),
    unlike a real 200-character post that is already shorter than a summary.
    """
    normalized = " ".join(text.casefold().split())
    if not normalized or normalized.startswith(_VISUAL_STUB_PREFIXES):
        return True
    return len(normalized) <= _STUB_SCAN_CHARS and any(
        marker in normalized for marker in _VISUAL_STUB_MARKERS
    )


def is_too_short_to_summarize(text: str) -> bool:
    """A real, non-visual post whose source is already under 400 characters."""
    return is_thin(text) and not is_visual_stub(text)
