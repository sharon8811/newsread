"""Article embeddings for semantic search, via the same OpenAI-compatible
endpoint as summarization (see llm.py). Requires the pgvector extension
(db.vector_enabled) and OPENAI_EMBEDDING_MODEL; without either, article
search silently stays keyword-only."""

import hashlib
import logging
import re
import time
from collections import OrderedDict

from sqlalchemy import case, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import db, llm
from .config import settings
from .models import Article, ArticleEmbedding

logger = logging.getLogger(__name__)

# How much text an embedding is *identified* by: text_for() builds it and
# input_hash/stale_input() hash it. What actually reaches the provider is
# capped separately by settings.embedding_input_max_chars, which tracks the
# model's context window; keeping the two apart means a context-budget change
# does not restate every stored hash and re-embed the whole archive.
MAX_CHARS = 6000
# Floor for the halving retry in embed_texts: below this, a "too long" reply
# is not about length any more and the error belongs to the caller.
MIN_INPUT_CHARS = 200
_OVER_CONTEXT_RE = re.compile(r"context length|context window|too long|too many tokens", re.I)
# fetcher.derive_excerpt collapses hnrss items to this metadata line. It is
# fine as a list-view excerpt but poison as embedding input: shared verbatim
# across every HN article, it dominates a short title and turns the vector
# into a universal "related coverage" hub. Kept dialect-portable (used both
# as a Python fullmatch and a Postgres ARE in stale_input).
_HN_META_EXCERPT = r"\d+ points( · \d+ comments)? · via Hacker News"
_HN_META_EXCERPT_RE = re.compile(_HN_META_EXCERPT)
QUERY_CACHE_TTL = 24 * 60 * 60
QUERY_CACHE_SIZE = 256
_query_cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()


def is_configured() -> bool:
    return bool(settings.openai_api_key and settings.openai_embedding_model and db.vector_enabled)


def text_for(article: Article) -> str:
    """Summaries are ideal embedding input (dense, clean, capped); fall back
    to the feed excerpt, then raw full text, for not-yet-summarized articles.
    stale_input() mirrors this construction in SQL — keep the two in sync
    (test_stale_input_sql_matches_text_for pins the parity)."""
    excerpt = article.excerpt or ""
    if _HN_META_EXCERPT_RE.fullmatch(excerpt):
        excerpt = ""
    body = article.summary_medium or excerpt or article.full_text[:4000]
    return f"{article.title}\n\n{body}"[:MAX_CHARS]


def input_hash_for(article: Article) -> str:
    return hashlib.md5(text_for(article).encode("utf-8")).hexdigest()


def stale_input():
    """SQL predicate (Article joined to ArticleEmbedding): the stored vector
    was embedded from different text than text_for(article) produces today —
    a summary landed after embedding, or a repair rewrote the excerpt. Must
    hash exactly what input_hash_for() stores; both md5 UTF-8 bytes, and
    left()/coalesce(nullif()) reproduce Python slicing and `or` fallbacks
    over these non-null text columns."""
    excerpt = case(
        (Article.excerpt.op("~")(f"^{_HN_META_EXCERPT}$"), ""),
        else_=Article.excerpt,
    )
    body = func.coalesce(
        func.nullif(Article.summary_medium, ""),
        func.nullif(excerpt, ""),
        func.left(Article.full_text, 4000),
    )
    text = func.left(Article.title + "\n\n" + body, MAX_CHARS)
    return or_(
        ArticleEmbedding.input_hash.is_(None),
        ArticleEmbedding.input_hash != func.md5(text),
    )


def over_context(exc: Exception) -> bool:
    """Whether the provider rejected the request for length alone. Ollama says
    "the input length exceeds the context length"; OpenAI, "maximum context
    length is N tokens". Both are retryable with less text — every other 400
    is not."""
    return bool(_OVER_CONTEXT_RE.search(str(exc)))


async def embed_texts(texts: list[str], *, shrink: bool = True) -> list[list[float]]:
    """Embed texts, halving the payload while the provider says it is too long.

    The char budget only approximates a token window, and how badly it
    approximates depends on the text: code, markdown, and dense scripts pack
    far more tokens per character than the prose it was sized for. Retrying
    smaller costs one round trip and keeps a shortened vector where giving up
    would leave the text unsearchable.

    A shrink applies to the whole request, so callers that can retry their
    items separately pass shrink=False and let only the offending item lose
    text, rather than every item that happened to share the request."""
    budget = settings.embedding_input_max_chars
    while True:
        payload = [text[:budget] for text in texts]
        try:
            response = await llm.get_client().embeddings.create(
                model=settings.openai_embedding_model,
                input=payload,
            )
        except Exception as exc:
            if not shrink or budget <= MIN_INPUT_CHARS or not over_context(exc):
                raise
            budget //= 2
            logger.warning("Embedding input over context; retrying at %d chars", budget)
            continue
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


async def _embed_one_by_one(
    articles: list[Article], texts: list[str]
) -> list[tuple[Article, str, list[float]]]:
    """Re-embed a failed batch article by article, dropping the ones that keep
    failing. The batch is picked newest-first and re-picked every refresh, so
    an article the provider will never accept otherwise sits at the head of it
    forever, costing every article behind it its vector too."""
    embedded: list[tuple[Article, str, list[float]]] = []
    for article, text in zip(articles, texts, strict=True):
        try:
            [vector] = await embed_texts([text])
        except Exception as exc:
            logger.warning("Skipping article %s: embedding failed: %s", article.id, exc)
            continue
        embedded.append((article, text, vector))
    return embedded


async def embed_articles(session: AsyncSession, articles: list[Article]) -> int:
    """Upsert embeddings for the given articles; returns how many were written."""
    if not articles:
        return 0
    texts = [text_for(article) for article in articles]
    try:
        vectors = await embed_texts(texts, shrink=False)
    except Exception as exc:
        # Only length is an article's own fault. A 401, a 429, or an outage
        # fails the same way one article at a time, so splitting the batch
        # would just multiply a bad moment by fifty — leave those to the
        # caller's retry.
        if not over_context(exc):
            raise
        logger.warning("Batch embedding of %d articles is over context: %s", len(articles), exc)
        embedded = await _embed_one_by_one(articles, texts)
    else:
        embedded = list(zip(articles, texts, vectors, strict=False))
    if not embedded:
        return 0
    stmt = pg_insert(ArticleEmbedding).values(
        [
            {
                "article_id": article.id,
                "model": settings.openai_embedding_model,
                "embedding": vector,
                "input_hash": hashlib.md5(text.encode("utf-8")).hexdigest(),
            }
            for article, text, vector in embedded
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["article_id"],
        set_={
            "embedding": stmt.excluded.embedding,
            "model": stmt.excluded.model,
            "input_hash": stmt.excluded.input_hash,
            "embedded_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(embedded)
