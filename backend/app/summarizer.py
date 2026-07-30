"""Shared summary generation, used by the API (on demand) and the worker (batch)."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from . import llm, screenshot, youtube
from .config import settings
from .extractor import clip_for_llm, ensure_full_text, is_thin, is_too_short_to_summarize
from .models import Article, Feed

logger = logging.getLogger(__name__)


class ThinContentError(Exception):
    """The article's full text is unavailable — refusing to summarize a stub."""


class SummarySkipped(Exception):
    """The source is already shorter than a useful summary; no LLM call was made."""


def transcript_still_owed(article: Article) -> bool:
    """A video whose captions have not been fetched yet. Enrichment stamps
    full_text_fetched_at even when it comes up empty, so an unstamped video is
    one YouTube refused us — the transcript is still out there."""
    return (
        bool(youtube.video_id(article.url))
        and not article.full_text
        and article.full_text_fetched_at is None
    )


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
    if transcript_still_owed(article):
        # YouTube refused the caption request (see extractor._enrich_video).
        # Anything stored now is permanent — a summary is never regenerated and
        # a "too_short" stamp blocks even a manual retry — so the video would be
        # stuck with its description long after the captions became available.
        raise ThinContentError(
            "YouTube is throttling caption requests for this video — try again in a few minutes."
        )
    if is_too_short_to_summarize(text):
        article.summary_short = ""
        article.summary_medium = ""
        article.summary = ""
        article.summary_model = None
        article.summary_generated_at = None
        article.summary_skipped_reason = "too_short"
        await session.commit()
        raise SummarySkipped()
    # Read here rather than passed in, so every caller — the batch worker, the
    # on-demand endpoint and the URL importer — honors the feed's instructions
    # without threading them through three call sites.
    feed = await session.get(Feed, article.feed_id)
    instructions = feed.summary_instructions if feed is not None else None
    if is_thin(text):
        short, medium, full = await _summarize_from_screenshot(
            article, allow_vision, config=config, usage=usage, instructions=instructions
        )
    else:
        # Videos reach this point only with captions behind them (extractor
        # puts the transcript in full_text); without any, `text` is the feed's
        # own description and reads like an article.
        transcript = bool(youtube.video_id(article.url)) and bool(article.full_text)
        short, medium, full = await llm.summarize(
            article.title,
            clip_for_llm(text),
            url=article.url,
            author=article.author,
            published_at=article.published_at,
            config=config,
            usage=usage,
            instructions=instructions,
            source_kind="transcript" if transcript else "article",
        )
    if not full:
        raise RuntimeError("LLM returned an empty summary")

    article.summary_short = short
    article.summary_medium = medium
    article.summary = full
    # Summaries are written in the source's language, so this is what the
    # translate action compares its target against. Detected here (in-process,
    # no LLM call) so it is stored alongside the text it describes.
    article.summary_language = llm.detect_language(full)
    article.summary_model = config.model if config is not None else settings.openai_model
    article.summary_generated_at = datetime.now(UTC)
    article.summary_skipped_reason = None
    await session.commit()


async def _summarize_from_screenshot(
    article: Article,
    allow_vision: bool,
    *,
    config: llm.LLMConfig | None,
    usage: llm.TokenUsage | None,
    instructions: str | None = None,
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
        instructions=instructions,
    )
