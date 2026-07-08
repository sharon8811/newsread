"""Shared summary generation, used by the API (on demand) and the worker (batch)."""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from . import llm
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
) -> None:
    """Generate and store all three summary levels for an article.

    `config` selects the endpoint/key (a user's own key for on-demand
    summaries); None means the server-wide default (the worker's batch path).
    """
    text = await ensure_full_text(session, article, allow_refetch=allow_refetch)
    if is_thin(text):
        raise ThinContentError()

    short, medium, full = await llm.summarize(
        article.title, clip_for_llm(text), config=config, usage=usage
    )
    if not full:
        raise RuntimeError("LLM returned an empty summary")

    article.summary_short = short
    article.summary_medium = medium
    article.summary = full
    article.summary_model = config.model if config is not None else settings.openai_model
    article.summary_generated_at = datetime.now(timezone.utc)
    await session.commit()
