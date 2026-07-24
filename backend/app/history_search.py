"""Owner-scoped PostgreSQL keyword and vector retrieval for browser history."""

import logging
from datetime import date

from sqlalchemy import case, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import embeddings, history_embeddings, ranking
from .config import settings
from .models import BrowserHistoryEmbedding, BrowserHistoryPage

logger = logging.getLogger(__name__)

HISTORY_SEARCH_POOL = 200


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
