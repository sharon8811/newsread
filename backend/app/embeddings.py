"""Article embeddings for semantic search, via the same OpenAI-compatible
endpoint as summarization (see llm.py). Requires the pgvector extension
(db.vector_enabled) and OPENAI_EMBEDDING_MODEL; without either, article
search silently stays keyword-only."""

import logging
import time
from collections import OrderedDict

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import db, llm
from .config import settings
from .models import Article, ArticleEmbedding

logger = logging.getLogger(__name__)

# Keep inputs comfortably under typical 8k-token embedding model limits.
MAX_CHARS = 6000
QUERY_CACHE_TTL = 24 * 60 * 60
QUERY_CACHE_SIZE = 256
_query_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()


def is_configured() -> bool:
    return bool(
        settings.openai_api_key and settings.openai_embedding_model and db.vector_enabled
    )


def text_for(article: Article) -> str:
    """Summaries are ideal embedding input (dense, clean, capped); fall back
    to the feed excerpt, then raw full text, for not-yet-summarized articles."""
    body = article.summary_medium or article.excerpt or article.full_text[:4000]
    return f"{article.title}\n\n{body}"[:MAX_CHARS]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    response = await llm.get_client().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


async def embed_query(text: str) -> list[float]:
    """Embed a normalized search query with a small process-local TTL cache."""
    normalized = " ".join(text.casefold().split())
    key = f"{settings.openai_embedding_model}:{normalized}"
    now = time.monotonic()
    cached = _query_cache.get(key)
    if cached and now - cached[0] < QUERY_CACHE_TTL:
        _query_cache.move_to_end(key)
        return cached[1]
    [vector] = await embed_texts([normalized])
    _query_cache[key] = (now, vector)
    _query_cache.move_to_end(key)
    while len(_query_cache) > QUERY_CACHE_SIZE:
        _query_cache.popitem(last=False)
    return vector


async def embed_articles(session: AsyncSession, articles: list[Article]) -> int:
    """Upsert embeddings for the given articles; returns how many were written."""
    if not articles:
        return 0
    vectors = await embed_texts([text_for(article) for article in articles])
    stmt = pg_insert(ArticleEmbedding).values(
        [
            {
                "article_id": article.id,
                "model": settings.openai_embedding_model,
                "embedding": vector,
            }
            for article, vector in zip(articles, vectors)
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["article_id"],
        set_={
            "embedding": stmt.excluded.embedding,
            "model": stmt.excluded.model,
            "embedded_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(articles)
