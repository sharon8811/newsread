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
    session: AsyncSession, article: Article, allow_refetch: bool = True
) -> None:
    """Generate and store all three summary levels for an article."""
    text = await ensure_full_text(session, article, allow_refetch=allow_refetch)
    if is_thin(text):
        raise ThinContentError()

    short, medium, full = await llm.summarize(article.title, clip_for_llm(text))
    if not full:
        raise RuntimeError("LLM returned an empty summary")

    article.summary_short = short
    article.summary_medium = medium
    article.summary = full
    article.summary_model = settings.openai_model
    article.summary_generated_at = datetime.now(timezone.utc)
    await session.commit()
