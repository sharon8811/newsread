"""Embedding input and persistence for private browser-history content.

Legacy page vectors remain readable during the migration window. New captures
are embedded once per content-addressed document, in chunks aligned to the
browser's captured block anchors.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings
from .config import settings
from .history_content import canonicalize_history_document, decompress_history_document
from .history_ingest import get_history_ingest_service
from .history_policy import history_content_hash, history_embedding_text
from .history_storage import EncryptedHistoryStorage
from .models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryEmbedding,
    BrowserHistoryPage,
)

logger = logging.getLogger(__name__)

DOCUMENT_EXTRACTION_VERSION = "history-dom-v2"
DOCUMENT_CHUNK_MAX_CHARS = embeddings.MAX_CHARS
DOCUMENT_EMBED_REQUEST_BATCH = 32


@dataclass(frozen=True)
class HistoryDocumentChunk:
    index: int
    text: str
    input_hash: str
    block_start_id: str
    block_end_id: str


def is_configured() -> bool:
    return embeddings.is_configured()


def text_for(page: BrowserHistoryPage) -> str:
    return history_embedding_text(page.title, page.hostname, page.text)


def input_hash_for(page: BrowserHistoryPage) -> str:
    return history_content_hash(page.title, page.hostname, page.text)


def stale_input():
    return or_(
        BrowserHistoryEmbedding.input_hash.is_(None),
        BrowserHistoryEmbedding.input_hash != BrowserHistoryPage.content_hash,
    )


async def embed_pages(
    session: AsyncSession,
    pages: list[BrowserHistoryPage],
) -> int:
    if not pages:
        return 0
    texts = [text_for(page) for page in pages]
    vectors = await embeddings.embed_texts(texts)
    statement = pg_insert(BrowserHistoryEmbedding).values(
        [
            {
                "page_id": page.id,
                "model": settings.openai_embedding_model,
                "embedding": vector,
                "input_hash": input_hash_for(page),
            }
            for page, vector in zip(pages, vectors, strict=False)
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["page_id"],
            set_={
                "embedding": statement.excluded.embedding,
                "model": statement.excluded.model,
                "input_hash": statement.excluded.input_hash,
                "embedded_at": func.now(),
            },
        )
    )
    await session.commit()
    return len(pages)


def document_is_eligible(document: BrowserHistoryDocument) -> bool:
    """Only structured v2 captures receive semantic vectors.

    Inline legacy conversions deliberately remain on the legacy search path:
    their single truncated block is not sufficient grounding for later
    summary/citation features.
    """
    return (
        document.storage_status == "ready"
        and document.extraction_version == DOCUMENT_EXTRACTION_VERSION
    )


def document_chunks(document: BrowserHistoryDocument, payload: bytes) -> list[HistoryDocumentChunk]:
    canonical = canonicalize_history_document(payload)
    if canonical.content_hash != document.content_hash:
        raise ValueError("stored history document hash does not match its database row")
    if canonical.extraction_version != DOCUMENT_EXTRACTION_VERSION:
        return []

    chunks: list[HistoryDocumentChunk] = []
    pending: list[tuple[str, str]] = []
    pending_chars = 0

    def emit() -> None:
        nonlocal pending, pending_chars
        if not pending:
            return
        text = "\n".join(value for _, value in pending)
        chunks.append(
            HistoryDocumentChunk(
                index=len(chunks),
                text=text,
                # Documents are immutable and content-addressed. Keeping the
                # source digest here makes stale-row detection cheap in SQL;
                # chunk identity is already pinned by index and block range.
                input_hash=document.content_hash,
                block_start_id=pending[0][0],
                block_end_id=pending[-1][0],
            )
        )
        pending = []
        pending_chars = 0

    for block in canonical.value["blocks"]:
        block_id = block["id"]
        remaining = block["text"]
        while remaining:
            room = DOCUMENT_CHUNK_MAX_CHARS - pending_chars - (1 if pending else 0)
            if room <= 0:
                emit()
                room = DOCUMENT_CHUNK_MAX_CHARS
            segment = remaining[:room]
            pending.append((block_id, segment))
            pending_chars += len(segment) + (1 if len(pending) > 1 else 0)
            remaining = remaining[len(segment) :]
            if remaining:
                emit()
    emit()
    return chunks


async def load_document_chunks(
    session: AsyncSession,
    document: BrowserHistoryDocument,
    *,
    storage: EncryptedHistoryStorage | None = None,
) -> list[HistoryDocumentChunk]:
    if not document_is_eligible(document):
        return []
    storage = storage or get_history_ingest_service().storage
    compressed = await storage.get(
        session,
        user_id=document.user_id,
        object_type="document",
        object_hash=document.content_hash,
        object_key=document.object_key,
    )
    payload = decompress_history_document(
        compressed,
        max_bytes=settings.history_object_max_bytes,
    )
    return document_chunks(document, payload)


async def embed_documents(
    session: AsyncSession,
    documents: list[BrowserHistoryDocument],
    *,
    storage: EncryptedHistoryStorage | None = None,
    commit: bool = True,
) -> int:
    """Replace current-model chunks for eligible documents.

    Object reads and provider calls happen before writes, so a failure leaves
    the previous vectors intact and the catch-up query can safely retry.
    """
    eligible = [document for document in documents if document_is_eligible(document)]
    if not eligible:
        return 0

    chunks_by_document: list[tuple[BrowserHistoryDocument, list[HistoryDocumentChunk]]] = []
    all_chunks: list[HistoryDocumentChunk] = []
    for document in eligible:
        chunks = await load_document_chunks(session, document, storage=storage)
        if chunks:
            chunks_by_document.append((document, chunks))
            all_chunks.extend(chunks)
    if not all_chunks:
        return 0

    vectors: list[list[float]] = []
    texts = [chunk.text for chunk in all_chunks]
    for offset in range(0, len(texts), DOCUMENT_EMBED_REQUEST_BATCH):
        vectors.extend(
            await embeddings.embed_texts(texts[offset : offset + DOCUMENT_EMBED_REQUEST_BATCH])
        )
    if len(vectors) != len(all_chunks):
        raise RuntimeError("embedding provider returned an incomplete history batch")
    vector_index = 0
    for document, chunks in chunks_by_document:
        await session.execute(
            delete(BrowserHistoryDocumentEmbedding).where(
                BrowserHistoryDocumentEmbedding.document_id == document.id,
            )
        )
        values = []
        for chunk in chunks:
            values.append(
                {
                    "document_id": document.id,
                    "chunk_index": chunk.index,
                    "model": settings.openai_embedding_model,
                    "embedding": vectors[vector_index],
                    "input_hash": chunk.input_hash,
                    "block_start_id": chunk.block_start_id,
                    "block_end_id": chunk.block_end_id,
                }
            )
            vector_index += 1
        await session.execute(pg_insert(BrowserHistoryDocumentEmbedding).values(values))

    if commit:
        await session.commit()
    return len(chunks_by_document)


async def embed_document(
    document_id: int,
    *,
    storage: EncryptedHistoryStorage | None = None,
) -> int:
    """ARQ entry point for an eagerly queued first document link."""
    from . import db

    if not is_configured():
        return 0
    async with db.SessionLocal() as session:
        document = await session.scalar(
            select(BrowserHistoryDocument)
            .where(BrowserHistoryDocument.id == document_id)
            .with_for_update()
        )
        if document is None:
            return 0
        current = await session.scalar(
            select(BrowserHistoryDocumentEmbedding.id)
            .where(
                BrowserHistoryDocumentEmbedding.document_id == document.id,
                BrowserHistoryDocumentEmbedding.model == settings.openai_embedding_model,
                BrowserHistoryDocumentEmbedding.input_hash == document.content_hash,
            )
            .limit(1)
        )
        if current is not None:
            return 0
        try:
            return await embed_documents(session, [document], storage=storage)
        except Exception:
            await session.rollback()
            raise
