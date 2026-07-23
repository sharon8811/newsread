"""Embedding input and persistence for private browser-history pages."""

import logging

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings
from .config import settings
from .history_policy import history_content_hash, history_embedding_text
from .models import BrowserHistoryEmbedding, BrowserHistoryPage

logger = logging.getLogger(__name__)


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
