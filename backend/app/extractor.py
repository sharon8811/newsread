"""Original-page enrichment: Scrapling fetches once, we take prose + og:image."""

import logging
from datetime import UTC, datetime, timedelta

import trafilatura
from scrapling.fetchers import AsyncFetcher
from sqlalchemy.ext.asyncio import AsyncSession

from .fetcher import strip_html
from .models import Article

logger = logging.getLogger(__name__)

# Feed content longer than this is treated as the real article body;
# shorter content (e.g. hnrss link stubs) triggers a fetch of the original page.
MIN_USEFUL_CHARS = 800

MAX_LLM_CHARS = 24_000

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

# Don't re-hit a page that recently failed to yield text (site likely blocks bots).
REFETCH_COOLDOWN = timedelta(hours=6)


async def fetch_page(url: str) -> tuple[str, str | None, str | None]:
    """Fetch a page; return (extracted prose, lead image URL, page title)."""
    try:
        page = await AsyncFetcher.get(url, impersonate="chrome")
    except Exception as exc:
        logger.warning("Page fetch of %s failed: %s", url, exc)
        return "", None, None
    if page.status != 200:
        logger.warning("Page fetch of %s returned %s", url, page.status)
        return "", None, None

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


async def enrich_article(session: AsyncSession, article: Article) -> None:
    """Fill full_text and image_url from the original page, fetching it at most once."""
    need_text = not article.full_text and is_thin(strip_html(article.content_html))
    need_image = not article.image_url
    if need_text or need_image:
        text, image, _ = await fetch_page(article.url)
        if need_text:
            article.full_text = text
        if need_image and image:
            article.image_url = image[:2048]
    # Stamp unconditionally: the worker's batch query and the feeds
    # pending_count treat a NULL stamp as "still pending", so an article that
    # needs nothing (rich feed body, image already set) or whose page yields
    # no image would otherwise stay pending — and be re-fetched — forever.
    article.full_text_fetched_at = datetime.now(UTC)
    await session.commit()


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
    return not normalized or normalized.startswith(_VISUAL_STUB_PREFIXES)


def is_too_short_to_summarize(text: str) -> bool:
    """A real, non-visual post whose source is already under 400 characters."""
    return is_thin(text) and not is_visual_stub(text)
