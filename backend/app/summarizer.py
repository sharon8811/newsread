"""Shared summary generation, used by the API (on demand) and the worker (batch)."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from . import llm, pdf, processing_events, screenshot, youtube
from .config import settings
from .extractor import (
    clip_for_llm,
    ensure_full_text,
    is_thin,
    is_too_short_to_summarize,
    is_visual_stub,
)
from .models import Article, Feed

logger = logging.getLogger(__name__)


class ThinContentError(Exception):
    """The article's full text is unavailable — refusing to summarize a stub."""


class SummarySkipped(Exception):
    """The source is already shorter than a useful summary; no LLM call was made."""


# The user-facing explanation for ThinContentError raisers that don't carry
# their own message. Shared with error_handlers so the streaming endpoint and
# the 422 handler tell the reader the same thing.
THIN_CONTENT_DETAIL = (
    "Couldn't fetch the article's full text — the site may block automated "
    "readers. Open the original instead."
)

_CAPTIONS_THROTTLED_DETAIL = (
    "YouTube is throttling caption requests for this video — try again in a few minutes."
)

# Streamed to the reader when a force-regenerate finds the page unusable but a
# good summary is already stored: nothing was lost, and this says why nothing
# changed either.
SUMMARY_KEPT_DETAIL = (
    "The article's page can't be read right now — it may be missing or blocked "
    "— so the existing summary was kept."
)


def transcript_still_owed(article: Article) -> bool:
    """A video whose captions have not been fetched yet. Enrichment stamps
    full_text_fetched_at even when it comes up empty, so an unstamped video is
    one YouTube refused us — the transcript is still out there."""
    return (
        bool(youtube.video_id(article.url))
        and not article.full_text
        and article.full_text_fetched_at is None
    )


def unreadable_source(article: Article, text: str) -> str | None:
    """The skip reason for a video or document we fetched and could not read,
    or None when there is still something worth summarizing.

    Only reached when the feed's own body is empty or a browser shell — the
    is_visual_stub line, not merely the is_thin one. A video whose captions
    are off but whose entry carries a real description is summarized from that
    description when it's long enough and called "too_short" when it isn't;
    either way the reader is better served than by a status that claims there
    was no description. When there is genuinely nothing, saying so beats the
    alternatives, because neither of these sources has a page a screenshot
    could rescue: a watch page is a player, and a PDF renders inside a plugin
    viewer that headless Chrome captures as a blank rectangle.

    A PDF is recognized from its URL rather than from what was fetched — the
    bytes are long gone by now — so a document served without a .pdf suffix
    falls back to the generic thin-source handling.
    """
    if article.full_text or article.full_text_fetched_at is None or not is_visual_stub(text):
        return None
    if youtube.video_id(article.url):
        return "no_transcript"
    if pdf.looks_like_pdf(article.url):
        return "unreadable_pdf"
    return None


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
        raise ThinContentError(_CAPTIONS_THROTTLED_DETAIL)
    unreadable = unreadable_source(article, text)
    if unreadable or is_too_short_to_summarize(text):
        if article.summary:
            # The page rotted to a stub under a stored summary (typically a
            # force-regenerate of a screenshot-summarized article, whose
            # full_text was always empty). Keep the summary — it's the only
            # good copy left — and stamp the reason so the batch worker
            # doesn't pick legacy rows up again every cycle.
            _retain_unusable(session, article)
            await session.commit()
            raise SummarySkipped()
        _mark_skipped(session, article, unreadable or "too_short")
        await session.commit()
        raise SummarySkipped()
    # Read here rather than passed in, so every caller — the batch worker, the
    # on-demand endpoint and the URL importer — honors the feed's instructions
    # without threading them through three call sites.
    feed = await session.get(Feed, article.feed_id)
    instructions = feed.summary_instructions if feed is not None else None
    try:
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
    except llm.UnusableContentError as exc:
        # The model was handed a page that isn't the article (a 404 screen, a
        # paywall, a bot check) and said so instead of summarizing it. Stamp
        # the reason — the batch worker stops retrying, the clients explain
        # the failure — and return normally so the tokens spent finding out
        # are still metered as a completed call.
        if article.summary:
            # A force-regenerate of an article whose page has since rotted
            # away (screenshot-summarized pages re-render the live page every
            # time). The stored summary is the only good copy left — keep it,
            # and stamp the reason so worker-eligible legacy rows (summary
            # but no summary_short) don't burn an LLM call every cycle.
            logger.info(
                "Article %s page is unusable (%s); keeping the existing summary", article.id, exc
            )
            _retain_unusable(session, article)
            await session.commit()
            return
        logger.info("Article %s page is unusable (%s); summary skipped", article.id, exc)
        _mark_skipped(session, article, "unusable_page")
        await session.commit()
        return
    if not full:
        raise RuntimeError("LLM returned an empty summary")

    _store_summary(article, short, medium, full, config)
    await session.commit()


async def stream_summaries(
    session: AsyncSession,
    article: Article,
    *,
    config: llm.LLMConfig | None = None,
    usage: llm.TokenUsage | None = None,
):
    """generate_summaries as an event stream for the SSE endpoint: yields
    status / delta / skipped / done dicts while persisting exactly what the
    non-streaming path would. Refetch and vision are always allowed — this
    only runs on demand from an open article view."""
    yield {"type": "status", "stage": "reading"}
    text = await ensure_full_text(session, article, allow_refetch=True)
    if transcript_still_owed(article):
        raise ThinContentError(_CAPTIONS_THROTTLED_DETAIL)
    unreadable = unreadable_source(article, text)
    if unreadable or is_too_short_to_summarize(text):
        if article.summary:
            # Same preservation rule as generate_summaries: the refetch came
            # back a stub, so the stored summary is the only good copy left.
            _retain_unusable(session, article)
            await session.commit()
            yield {"type": "error", "detail": SUMMARY_KEPT_DETAIL}
            return
        reason = unreadable or "too_short"
        _mark_skipped(session, article, reason)
        await session.commit()
        yield {"type": "skipped", "reason": reason}
        return
    feed = await session.get(Feed, article.feed_id)
    instructions = feed.summary_instructions if feed is not None else None
    try:
        if is_thin(text):
            # The vision fallback answers in one piece; the reader still gets
            # a stage change and the finished text as a single delta.
            yield {"type": "status", "stage": "rendering"}
            short, medium, full = await _summarize_from_screenshot(
                article, True, config=config, usage=usage, instructions=instructions
            )
            yield {"type": "delta", "text": full}
        else:
            yield {"type": "status", "stage": "summarizing"}
            transcript = bool(youtube.video_id(article.url)) and bool(article.full_text)
            short = medium = full = ""
            async for event in llm.summarize_stream(
                article.title,
                clip_for_llm(text),
                url=article.url,
                author=article.author,
                published_at=article.published_at,
                config=config,
                usage=usage,
                instructions=instructions,
                source_kind="transcript" if transcript else "article",
            ):
                if event["type"] == "delta":
                    yield event
                else:
                    short, medium, full = event["levels"]
    except llm.UnusableContentError as exc:
        if article.summary:
            # Same preservation rule as generate_summaries: a regenerate that
            # hit a rotted page must not trade a good summary for a skip. The
            # error event tells the reader why nothing changed.
            logger.info(
                "Article %s page is unusable (%s); keeping the existing summary", article.id, exc
            )
            _retain_unusable(session, article)
            await session.commit()
            yield {"type": "error", "detail": SUMMARY_KEPT_DETAIL}
            return
        logger.info("Article %s page is unusable (%s); summary skipped", article.id, exc)
        _mark_skipped(session, article, "unusable_page")
        await session.commit()
        yield {"type": "skipped", "reason": "unusable_page"}
        return
    if not full:
        raise RuntimeError("LLM returned an empty summary")
    _store_summary(article, short, medium, full, config)
    await session.commit()
    yield {"type": "done"}


def _retain_unusable(session: AsyncSession, article: Article) -> None:
    """A (re)generation attempt hit an unusable/stub page while a good
    summary is already stored: keep the summary, stamp the reason, and still
    date the attempt — the skipped-processing trend must count these."""
    article.summary_skipped_reason = "unusable_page"
    processing_events.add_event(
        session,
        stage=processing_events.STAGE_SUMMARIZE,
        outcome=processing_events.OUTCOME_SKIPPED,
        article_id=article.id,
        feed_id=article.feed_id,
        detail="unusable_page",
    )


def _mark_skipped(session: AsyncSession, article: Article, reason: str) -> None:
    article.summary_short = ""
    article.summary_medium = ""
    article.summary = ""
    article.summary_model = None
    article.summary_generated_at = None
    article.summary_skipped_reason = reason
    # The reason column says *that* an article was skipped but not when; the
    # event dates it for the processing-health trends. The caller's commit
    # (right after every _mark_skipped call) carries both.
    processing_events.add_event(
        session,
        stage=processing_events.STAGE_SUMMARIZE,
        outcome=processing_events.OUTCOME_SKIPPED,
        article_id=article.id,
        feed_id=article.feed_id,
        detail=reason,
    )


def _store_summary(
    article: Article, short: str, medium: str, full: str, config: llm.LLMConfig | None
) -> None:
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
