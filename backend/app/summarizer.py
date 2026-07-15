"""Shared summary generation, used by the API (on demand) and the worker (batch)."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from . import llm, screenshot
from .config import settings
from .extractor import clip_for_llm, ensure_full_text, is_thin
from .models import Article

logger = logging.getLogger(__name__)


class ThinContentError(Exception):
    """The article's full text is unavailable — refusing to summarize a stub."""


async def generate_summaries(
    session: AsyncSession,
    article: Article,
    allow_refetch: bool = True,
    *,
    config: llm.LLMConfig | None = None,
    usage: llm.TokenUsage | None = None,
    allow_vision: bool = False,
) -> None:
    """Generate and store all three summary levels for an article.

    `config` selects the endpoint/key (a user's own key for on-demand
    summaries); None means the server-wide default (the worker's batch path).
    `allow_vision` lets a thin page (image-only comic, chart) be summarized
    from a rendered screenshot when the model accepts images — on-demand
    only, so the batch worker never spends browser renders + vision tokens
    on every bot-blocked stub.
    """
    text = await ensure_full_text(session, article, allow_refetch=allow_refetch)
    if is_thin(text):
        short, medium, full = await _summarize_from_screenshot(
            article, allow_vision, config=config, usage=usage
        )
    else:
        short, medium, full = await llm.summarize(
            article.title,
            clip_for_llm(text),
            url=article.url,
            author=article.author,
            published_at=article.published_at,
            config=config,
            usage=usage,
        )
    if not full:
        raise RuntimeError("LLM returned an empty summary")

    article.summary_short = short
    article.summary_medium = medium
    article.summary = full
    article.summary_model = config.model if config is not None else settings.openai_model
    article.summary_generated_at = datetime.now(UTC)
    await session.commit()


async def _summarize_from_screenshot(
    article: Article,
    allow_vision: bool,
    *,
    config: llm.LLMConfig | None,
    usage: llm.TokenUsage | None,
) -> tuple[str, str, str]:
    """The image-only fallback: render the page and let a vision model read it.
    Raises ThinContentError when vision isn't available or the render fails."""
    vision_capable = config.supports_vision if config is not None else settings.openai_model_vision
    if not (allow_vision and vision_capable):
        raise ThinContentError()
    shot = await screenshot.capture(article.url)
    if shot is None:
        raise ThinContentError()
    logger.info("Summarizing article %s from a page screenshot", article.id)
    return await llm.summarize_screenshot(
        article.title,
        shot,
        url=article.url,
        author=article.author,
        published_at=article.published_at,
        config=config,
        usage=usage,
    )
