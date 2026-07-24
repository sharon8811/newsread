"""Owner-scoped access to immutable browser-history documents.

All features that read captured page bodies go through this module so object
decryption, canonical validation, and prompt bounds cannot drift between the
detail, summary, and Q&A paths.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .history_content import (
    CanonicalHistoryDocument,
    canonicalize_history_document,
    decompress_history_document,
)
from .history_ingest import get_history_ingest_service
from .history_storage import EncryptedHistoryStorage
from .models import BrowserHistoryDocument, BrowserHistoryPage, BrowserHistoryPageDocument

HISTORY_PROMPT_MAX_CHARS = 60_000


async def owned_history_document(
    session: AsyncSession,
    *,
    user_id: int,
    document_id: int,
) -> BrowserHistoryDocument | None:
    """Return a ready document linked to one of the user's retained pages."""
    return await session.scalar(
        select(BrowserHistoryDocument)
        .join(
            BrowserHistoryPageDocument,
            BrowserHistoryPageDocument.document_id == BrowserHistoryDocument.id,
        )
        .join(BrowserHistoryPage, BrowserHistoryPage.id == BrowserHistoryPageDocument.page_id)
        .where(
            BrowserHistoryDocument.id == document_id,
            BrowserHistoryDocument.user_id == user_id,
            BrowserHistoryDocument.storage_status == "ready",
            BrowserHistoryPage.user_id == user_id,
        )
        .limit(1)
    )


async def load_history_document(
    session: AsyncSession,
    document: BrowserHistoryDocument,
    *,
    storage: EncryptedHistoryStorage | None = None,
) -> CanonicalHistoryDocument:
    storage = storage or get_history_ingest_service().storage
    compressed = await storage.get(
        session,
        user_id=document.user_id,
        object_type="document",
        object_hash=document.content_hash,
        object_key=document.object_key,
    )
    payload = decompress_history_document(compressed, max_bytes=settings.history_object_max_bytes)
    canonical = canonicalize_history_document(payload)
    if canonical.content_hash != document.content_hash:
        raise ValueError("stored history document hash does not match its database row")
    return canonical


async def latest_history_location(
    session: AsyncSession,
    *,
    user_id: int,
    document_id: int,
) -> BrowserHistoryPage | None:
    return await session.scalar(
        select(BrowserHistoryPage)
        .join(
            BrowserHistoryPageDocument,
            BrowserHistoryPageDocument.page_id == BrowserHistoryPage.id,
        )
        .where(
            BrowserHistoryPage.user_id == user_id,
            BrowserHistoryPageDocument.document_id == document_id,
        )
        .order_by(
            BrowserHistoryPageDocument.last_seen_at.desc(),
            BrowserHistoryPageDocument.id.desc(),
        )
        .limit(1)
    )


def build_history_prompt(
    canonical: CanonicalHistoryDocument,
    *,
    max_chars: int = HISTORY_PROMPT_MAX_CHARS,
) -> tuple[str, list[dict[str, str]]]:
    """Return bounded JSON Lines plus the exact blocks visible to the model."""
    selected: list[dict[str, str]] = []
    lines: list[str] = []
    used = 0
    for block in canonical.value["blocks"]:
        line = json.dumps(block, ensure_ascii=False, separators=(",", ":"))
        added = len(line) + (1 if selected else 0)
        if used + added > max_chars:
            break
        selected.append(block)
        lines.append(line)
        used += added
    return "\n".join(lines), selected


def history_prompt_corpus(
    canonical: CanonicalHistoryDocument,
    *,
    max_chars: int = HISTORY_PROMPT_MAX_CHARS,
) -> str:
    """Serialize whole hostile blocks as bounded JSON Lines.

    JSON escaping prevents captured text from forging the structural delimiters
    that separate one source block from the next.
    """
    return build_history_prompt(canonical, max_chars=max_chars)[0]
