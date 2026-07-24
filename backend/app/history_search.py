"""Owner-scoped PostgreSQL keyword and vector retrieval for browser history."""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings, history_embeddings, ranking
from .config import settings
from .models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryEmbedding,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
)

logger = logging.getLogger(__name__)

HISTORY_SEARCH_POOL = 200


@dataclass(frozen=True)
class HistorySearchHit:
    type: str
    id: int


def _escape_ilike(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def scoped_pages(
    user_id: int,
    *,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
):
    statement = select(BrowserHistoryPage).where(BrowserHistoryPage.user_id == user_id)
    if hostname:
        statement = statement.where(
            or_(
                BrowserHistoryPage.hostname == hostname,
                BrowserHistoryPage.hostname.endswith(f".{hostname}"),
            )
        )
    if date_from:
        statement = statement.where(func.date(BrowserHistoryPage.last_visited_at) >= date_from)
    if date_to:
        statement = statement.where(func.date(BrowserHistoryPage.last_visited_at) <= date_to)
    return statement


async def _keyword_ids(
    session: AsyncSession,
    base,
    query: str,
) -> list[int]:
    tsquery = func.websearch_to_tsquery("english", query)
    useful = "%" not in query and "_" not in query
    if useful:
        useful = bool(await session.scalar(select(func.numnode(tsquery))))
    if useful:
        search_tsv = literal_column("browser_history_pages.search_tsv")
        statement = (
            base.with_only_columns(BrowserHistoryPage.id, maintain_column_froms=True)
            .where(search_tsv.op("@@")(tsquery))
            .order_by(
                func.ts_rank(search_tsv, tsquery).desc(),
                BrowserHistoryPage.last_visited_at.desc(),
                BrowserHistoryPage.id.desc(),
            )
            .limit(HISTORY_SEARCH_POOL)
        )
    else:
        pattern = f"%{_escape_ilike(query)}%"
        title_match = BrowserHistoryPage.title.ilike(pattern, escape="\\")
        hostname_match = BrowserHistoryPage.hostname.ilike(pattern, escape="\\")
        text_match = BrowserHistoryPage.text.ilike(pattern, escape="\\")
        relevance = case(
            (title_match, 3),
            (hostname_match, 2),
            else_=1,
        )
        statement = (
            base.with_only_columns(BrowserHistoryPage.id, maintain_column_froms=True)
            .where(or_(title_match, hostname_match, text_match))
            .order_by(
                relevance.desc(),
                BrowserHistoryPage.last_visited_at.desc(),
                BrowserHistoryPage.id.desc(),
            )
            .limit(HISTORY_SEARCH_POOL)
        )
    return list(await session.scalars(statement))


async def hybrid_search_ids(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[int]:
    base = scoped_pages(
        user_id,
        hostname=hostname,
        date_from=date_from,
        date_to=date_to,
    )
    keyword_ids = await _keyword_ids(session, base, query)
    if not history_embeddings.is_configured():
        return keyword_ids
    try:
        query_vector = await embeddings.embed_query(query)
    except Exception as exc:
        logger.warning(
            "History query embedding failed, using keyword search: %s",
            exc,
        )
        return keyword_ids

    vector_statement = (
        base.with_only_columns(BrowserHistoryPage.id, maintain_column_froms=True)
        .join(
            BrowserHistoryEmbedding,
            BrowserHistoryEmbedding.page_id == BrowserHistoryPage.id,
        )
        .where(BrowserHistoryEmbedding.model == settings.openai_embedding_model)
        .order_by(BrowserHistoryEmbedding.embedding.cosine_distance(query_vector))
        .limit(HISTORY_SEARCH_POOL)
    )
    vector_ids = list(await session.scalars(vector_statement))
    return ranking.rrf_fuse(vector_ids, keyword_ids)


def _location_filters(
    user_id: int,
    *,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
):
    filters = [
        BrowserHistoryDocument.user_id == user_id,
        BrowserHistoryPage.user_id == user_id,
    ]
    if hostname:
        filters.append(
            or_(
                BrowserHistoryPage.hostname == hostname,
                BrowserHistoryPage.hostname.endswith(f".{hostname}"),
            )
        )
    if date_from:
        filters.append(func.date(BrowserHistoryPage.last_visited_at) >= date_from)
    if date_to:
        filters.append(func.date(BrowserHistoryPage.last_visited_at) <= date_to)
    return filters


def _scoped_document_ids(
    user_id: int,
    *,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
):
    return (
        select(BrowserHistoryDocument.id)
        .join(
            BrowserHistoryPageDocument,
            BrowserHistoryPageDocument.document_id == BrowserHistoryDocument.id,
        )
        .join(
            BrowserHistoryPage,
            BrowserHistoryPage.id == BrowserHistoryPageDocument.page_id,
        )
        .where(
            *_location_filters(
                user_id,
                hostname=hostname,
                date_from=date_from,
                date_to=date_to,
            )
        )
        .group_by(BrowserHistoryDocument.id)
    )


async def _document_keyword_ids(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[int]:
    statement = _scoped_document_ids(
        user_id,
        hostname=hostname,
        date_from=date_from,
        date_to=date_to,
    )
    tsquery = func.websearch_to_tsquery("simple", query)
    useful = "%" not in query and "_" not in query
    if useful:
        useful = bool(await session.scalar(select(func.numnode(tsquery))))
    if useful:
        page_tsv = literal_column("browser_history_pages.search_tsv")
        document_match = BrowserHistoryDocument.search_tsv.op("@@")(tsquery)
        page_match = page_tsv.op("@@")(tsquery)
        relevance = func.greatest(
            func.max(func.ts_rank(BrowserHistoryDocument.search_tsv, tsquery)),
            func.max(func.ts_rank(page_tsv, tsquery)),
        )
        statement = statement.where(or_(document_match, page_match)).order_by(
            relevance.desc(),
            func.max(BrowserHistoryPage.last_visited_at).desc(),
            BrowserHistoryDocument.id.desc(),
        )
    else:
        pattern = f"%{_escape_ilike(query)}%"
        title_match = BrowserHistoryPage.title.ilike(pattern, escape="\\")
        hostname_match = BrowserHistoryPage.hostname.ilike(pattern, escape="\\")
        excerpt_match = BrowserHistoryDocument.text_excerpt.ilike(pattern, escape="\\")
        relevance = func.max(
            case(
                (title_match, 3),
                (hostname_match, 2),
                else_=1,
            )
        )
        statement = statement.where(or_(title_match, hostname_match, excerpt_match)).order_by(
            relevance.desc(),
            func.max(BrowserHistoryPage.last_visited_at).desc(),
            BrowserHistoryDocument.id.desc(),
        )
    return list(await session.scalars(statement.limit(HISTORY_SEARCH_POOL)))


async def _document_vector_ids(
    session: AsyncSession,
    *,
    user_id: int,
    query_vector: list[float],
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[int]:
    distance = BrowserHistoryDocumentEmbedding.embedding.cosine_distance(query_vector)
    statement = (
        _scoped_document_ids(
            user_id,
            hostname=hostname,
            date_from=date_from,
            date_to=date_to,
        )
        .join(
            BrowserHistoryDocumentEmbedding,
            BrowserHistoryDocumentEmbedding.document_id == BrowserHistoryDocument.id,
        )
        .where(BrowserHistoryDocumentEmbedding.model == settings.openai_embedding_model)
        .order_by(
            func.min(distance),
            func.max(BrowserHistoryPage.last_visited_at).desc(),
            BrowserHistoryDocument.id.desc(),
        )
    )
    return list(await session.scalars(statement.limit(HISTORY_SEARCH_POOL)))


async def _legacy_page_ids(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    query_vector: list[float] | None,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
) -> tuple[list[int], list[int]]:
    base = scoped_pages(
        user_id,
        hostname=hostname,
        date_from=date_from,
        date_to=date_to,
    ).where(BrowserHistoryPage.current_document_id.is_(None))
    keyword_ids = await _keyword_ids(session, base, query)
    if query_vector is None:
        return keyword_ids, []
    vector_statement = (
        base.with_only_columns(BrowserHistoryPage.id, maintain_column_froms=True)
        .join(
            BrowserHistoryEmbedding,
            BrowserHistoryEmbedding.page_id == BrowserHistoryPage.id,
        )
        .where(BrowserHistoryEmbedding.model == settings.openai_embedding_model)
        .order_by(BrowserHistoryEmbedding.embedding.cosine_distance(query_vector))
        .limit(HISTORY_SEARCH_POOL)
    )
    return keyword_ids, list(await session.scalars(vector_statement))


def _tag(kind: str, item_id: int) -> int:
    return item_id * 2 + (1 if kind == "page" else 0)


def _untag(value: int) -> HistorySearchHit:
    return HistorySearchHit(type="page" if value % 2 else "document", id=value // 2)


async def hybrid_search(
    session: AsyncSession,
    *,
    user_id: int,
    query: str,
    hostname: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[HistorySearchHit]:
    """Return one hit per v2 document plus legacy/metadata-only page hits."""
    document_keyword_ids = await _document_keyword_ids(
        session,
        user_id=user_id,
        query=query,
        hostname=hostname,
        date_from=date_from,
        date_to=date_to,
    )
    query_vector: list[float] | None = None
    if history_embeddings.is_configured():
        try:
            query_vector = await embeddings.embed_query(query)
        except Exception as exc:
            logger.warning("History query embedding failed, using keyword search: %s", exc)

    document_vector_ids = (
        await _document_vector_ids(
            session,
            user_id=user_id,
            query_vector=query_vector,
            hostname=hostname,
            date_from=date_from,
            date_to=date_to,
        )
        if query_vector is not None
        else []
    )
    page_keyword_ids, page_vector_ids = await _legacy_page_ids(
        session,
        user_id=user_id,
        query=query,
        query_vector=query_vector,
        hostname=hostname,
        date_from=date_from,
        date_to=date_to,
    )
    # Document and legacy-page ranks come from separate SQL statements (and,
    # for keyword search, different tsvector configurations), so their raw
    # scores are not safely comparable. Prefer structured document hits during
    # the bounded dual-read window while preserving each source's own rank;
    # Phase 6 removes the legacy-page leg entirely.
    keyword_leg = [
        *(_tag("document", item_id) for item_id in document_keyword_ids),
        *(_tag("page", item_id) for item_id in page_keyword_ids),
    ]
    vector_leg = [
        *(_tag("document", item_id) for item_id in document_vector_ids),
        *(_tag("page", item_id) for item_id in page_vector_ids),
    ]
    ranked = ranking.rrf_fuse(vector_leg, keyword_leg)
    return [_untag(value) for value in ranked[:HISTORY_SEARCH_POOL]]
