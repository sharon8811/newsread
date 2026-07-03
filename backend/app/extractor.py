"""Full article text: Scrapling fetches the page, trafilatura extracts the prose."""

import logging
from datetime import datetime, timezone

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


async def ensure_full_text(session: AsyncSession, article: Article) -> str:
    """Return the best available article text, fetching and caching it if needed."""
    if article.full_text:
        return article.full_text

    fallback = strip_html(article.content_html)
    if len(fallback) >= MIN_USEFUL_CHARS:
        return fallback

    extracted = ""
    try:
        page = await AsyncFetcher.get(article.url, impersonate="chrome")
        if page.status == 200:
            extracted = (
                trafilatura.extract(page.html_content, include_comments=False) or ""
            )
        else:
            logger.warning("Full-text fetch of %s returned %s", article.url, page.status)
    except Exception as exc:
        logger.warning("Full-text fetch of %s failed: %s", article.url, exc)

    if extracted:
        article.full_text = extracted
        article.full_text_fetched_at = datetime.now(timezone.utc)
        await session.commit()
        return extracted

    return fallback


def clip_for_llm(text: str) -> str:
    if len(text) <= MAX_LLM_CHARS:
        return text
    return text[:MAX_LLM_CHARS] + "\n\n[article truncated]"


def is_thin(text: str) -> bool:
    """True when all we have is a link stub — too little to ground an LLM on."""
    return len(text.strip()) < 400
