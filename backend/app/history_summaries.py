"""Lazy, cached, cited summaries for private browser-history documents."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update

from . import db, llm
from .extractor import is_too_short_to_summarize
from .history_documents import (
    build_history_prompt,
    load_history_document,
)
from .history_embeddings import DOCUMENT_EXTRACTION_VERSION
from .models import BrowserHistoryDocument, BrowserHistorySummary

logger = logging.getLogger(__name__)

HISTORY_SUMMARY_PROMPT_VERSION = "history-summary-v1"
HISTORY_SUMMARY_BATCH = 10
HISTORY_SUMMARY_STALE_AFTER = timedelta(minutes=15)
MAX_CITATIONS = 12
_FENCED_JSON_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")
_HTML_RE = re.compile(r"<[A-Za-z/][^>]*>")


class HistorySummaryOutputError(llm.EmptyResponseError):
    pass


def parse_history_summary(raw: str, blocks: list[dict[str, str]]) -> tuple[str, list[dict]]:
    """Validate model output and derive every quote from trusted stored text."""
    if match := _FENCED_JSON_RE.fullmatch(raw.strip()):
        raw = match.group(1)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HistorySummaryOutputError("summary output is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"markdown", "block_ids"}:
        raise HistorySummaryOutputError("summary output has invalid fields")
    markdown = value["markdown"]
    block_ids = value["block_ids"]
    if (
        not isinstance(markdown, str)
        or not markdown.strip()
        or len(markdown) > 8_000
        or _MARKDOWN_LINK_RE.search(markdown)
        or _HTML_RE.search(markdown)
        or "```" in markdown
    ):
        raise HistorySummaryOutputError("summary markdown is invalid")
    if (
        not isinstance(block_ids, list)
        or not 1 <= len(block_ids) <= MAX_CITATIONS
        or any(not isinstance(block_id, str) for block_id in block_ids)
        or len(set(block_ids)) != len(block_ids)
    ):
        raise HistorySummaryOutputError("summary citations are invalid")
    markers = {int(value) for value in _CITATION_MARKER_RE.findall(markdown)}
    if markers != set(range(1, len(block_ids) + 1)):
        raise HistorySummaryOutputError("summary citation markers do not match its sources")

    blocks_by_id = {block["id"]: block for block in blocks}
    citations: list[dict] = []
    for block_id in block_ids:
        block = blocks_by_id.get(block_id)
        if block is None:
            raise HistorySummaryOutputError("summary cites an unknown block")
        text = block["text"]
        quote = text[:240].rstrip()
        citations.append(
            {
                "block_id": block_id,
                "quote": quote,
                "prefix": None,
                "suffix": text[len(quote) : len(quote) + 100].strip() or None,
            }
        )
    return markdown.strip(), citations


async def generate_history_summary(summary_id: int) -> None:
    """ARQ entry point. An atomic state transition makes duplicate jobs no-op."""
    now = datetime.now(UTC)
    stale_before = now - HISTORY_SUMMARY_STALE_AFTER
    async with db.SessionLocal() as session:
        document_id = await session.scalar(
            update(BrowserHistorySummary)
            .where(
                BrowserHistorySummary.id == summary_id,
                or_(
                    BrowserHistorySummary.status == "queued",
                    and_(
                        BrowserHistorySummary.status == "running",
                        BrowserHistorySummary.updated_at < stale_before,
                    ),
                ),
            )
            .values(status="running", error_code=None, updated_at=func.now())
            .returning(BrowserHistorySummary.document_id)
        )
        await session.commit()
        if document_id is None:
            return

        document = await session.get(BrowserHistoryDocument, document_id)
        row = await session.get(BrowserHistorySummary, summary_id)
        if document is None or row is None:
            return
        try:
            if document.extraction_version != DOCUMENT_EXTRACTION_VERSION:
                row.status = "error"
                row.error_code = "unsupported_legacy"
                await session.commit()
                return
            canonical = await load_history_document(session, document)
            if is_too_short_to_summarize(canonical.search_text):
                row.status = "too_short"
                row.error_code = "too_short"
                await session.commit()
                return
            config = await llm.resolve_config(session, document.user_id)
            if config is None:
                row.status = "error"
                row.error_code = "llm_unconfigured"
                await session.commit()
                return
            corpus, prompt_blocks = build_history_prompt(canonical)
            async with llm.usage_tracker(
                session,
                user_id=document.user_id,
                feature="history_summary",
                config=config,
                log_label=f"History summary for document {document.id}",
            ) as usage:
                raw = await llm.summarize_history_document(
                    corpus=corpus,
                    config=config,
                    usage=usage,
                )
                markdown, citations = parse_history_summary(
                    raw,
                    prompt_blocks,
                )
            row = await session.get(BrowserHistorySummary, summary_id)
            if row is None:
                return
            row.status = "ready"
            row.markdown = markdown
            row.citations = citations
            row.error_code = None
            row.generated_at = datetime.now(UTC)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            row = await session.get(BrowserHistorySummary, summary_id)
            if row is not None:
                row.status = "error"
                row.error_code = (
                    "invalid_model_output"
                    if isinstance(exc, HistorySummaryOutputError)
                    else "generation_failed"
                )
                await session.commit()
            logger.warning("History summary %s failed: %s", summary_id, exc)


async def generate_history_summaries_batch(ctx: dict | None = None) -> int:
    """Retry explicitly requested queued jobs and workers interrupted mid-call."""
    stale_before = datetime.now(UTC) - HISTORY_SUMMARY_STALE_AFTER
    async with db.SessionLocal() as session:
        ids = list(
            await session.scalars(
                select(BrowserHistorySummary.id)
                .where(
                    or_(
                        BrowserHistorySummary.status == "queued",
                        and_(
                            BrowserHistorySummary.status == "running",
                            BrowserHistorySummary.updated_at < stale_before,
                        ),
                    )
                )
                .order_by(BrowserHistorySummary.created_at)
                .limit(HISTORY_SUMMARY_BATCH)
            )
        )
    for summary_id in ids:
        await generate_history_summary(summary_id)
    return len(ids)
