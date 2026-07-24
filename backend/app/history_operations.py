"""Owner and operator metrics for browser-history storage pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import (
    BrowserHistoryDocument,
    BrowserHistoryDocumentEmbedding,
    BrowserHistoryImage,
    BrowserHistoryObjectDeletion,
    BrowserHistoryPageDocument,
)


@dataclass(frozen=True)
class HistoryOperationsMetrics:
    storage_used_bytes: int
    storage_quota_bytes: int
    document_count: int
    image_count: int
    embedding_backlog_count: int
    embedding_backlog_oldest_at: datetime | None
    deletion_backlog_count: int
    deletion_backlog_oldest_at: datetime | None


@dataclass(frozen=True)
class HistoryOperatorMetrics:
    embedding_backlog_count: int
    embedding_backlog_oldest_at: datetime | None
    deletion_backlog_count: int
    deletion_backlog_oldest_at: datetime | None
    users_near_storage_quota: int
    stored_bytes: int


def _embedding_backlog_filters(user_id: int | None = None):
    linked = (
        select(BrowserHistoryPageDocument.id)
        .where(BrowserHistoryPageDocument.document_id == BrowserHistoryDocument.id)
        .exists()
    )
    current = (
        select(BrowserHistoryDocumentEmbedding.id)
        .where(
            BrowserHistoryDocumentEmbedding.document_id == BrowserHistoryDocument.id,
            BrowserHistoryDocumentEmbedding.model == settings.openai_embedding_model,
            BrowserHistoryDocumentEmbedding.input_hash == BrowserHistoryDocument.content_hash,
        )
        .exists()
    )
    filters = [
        BrowserHistoryDocument.storage_status == "ready",
        BrowserHistoryDocument.extraction_version == "history-dom-v2",
        linked,
        ~current,
    ]
    if user_id is not None:
        filters.append(BrowserHistoryDocument.user_id == user_id)
    return filters


async def user_history_operations(
    session: AsyncSession,
    user_id: int,
) -> HistoryOperationsMetrics:
    document_count, document_bytes = (
        await session.execute(
            select(
                func.count(BrowserHistoryDocument.id),
                func.coalesce(func.sum(BrowserHistoryDocument.byte_size), 0),
            ).where(BrowserHistoryDocument.user_id == user_id)
        )
    ).one()
    image_count, image_bytes = (
        await session.execute(
            select(
                func.count(BrowserHistoryImage.id),
                func.coalesce(func.sum(BrowserHistoryImage.byte_size), 0),
            ).where(BrowserHistoryImage.user_id == user_id)
        )
    ).one()
    embedding_count, embedding_oldest = (
        await session.execute(
            select(
                func.count(BrowserHistoryDocument.id),
                func.min(BrowserHistoryDocument.created_at),
            ).where(*_embedding_backlog_filters(user_id))
        )
    ).one()
    deletion_count, deletion_oldest = (
        await session.execute(
            select(
                func.count(BrowserHistoryObjectDeletion.id),
                func.min(BrowserHistoryObjectDeletion.queued_at),
            ).where(BrowserHistoryObjectDeletion.owner_user_id == user_id)
        )
    ).one()
    return HistoryOperationsMetrics(
        storage_used_bytes=int(document_bytes or 0) + int(image_bytes or 0),
        storage_quota_bytes=settings.history_user_storage_max_bytes,
        document_count=int(document_count),
        image_count=int(image_count),
        embedding_backlog_count=int(embedding_count),
        embedding_backlog_oldest_at=embedding_oldest,
        deletion_backlog_count=int(deletion_count),
        deletion_backlog_oldest_at=deletion_oldest,
    )


async def operator_history_operations(
    session: AsyncSession,
) -> HistoryOperatorMetrics:
    embedding_count, embedding_oldest = (
        await session.execute(
            select(
                func.count(BrowserHistoryDocument.id),
                func.min(BrowserHistoryDocument.created_at),
            ).where(*_embedding_backlog_filters())
        )
    ).one()
    deletion_count, deletion_oldest = (
        await session.execute(
            select(
                func.count(BrowserHistoryObjectDeletion.id),
                func.min(BrowserHistoryObjectDeletion.queued_at),
            )
        )
    ).one()
    usage: dict[int, int] = {}
    for user_id, byte_count in await session.execute(
        select(
            BrowserHistoryDocument.user_id,
            func.coalesce(func.sum(BrowserHistoryDocument.byte_size), 0),
        ).group_by(BrowserHistoryDocument.user_id)
    ):
        usage[user_id] = int(byte_count)
    for user_id, byte_count in await session.execute(
        select(
            BrowserHistoryImage.user_id,
            func.coalesce(func.sum(BrowserHistoryImage.byte_size), 0),
        ).group_by(BrowserHistoryImage.user_id)
    ):
        usage[user_id] = usage.get(user_id, 0) + int(byte_count)
    threshold = settings.history_user_storage_max_bytes * settings.history_storage_alert_ratio
    return HistoryOperatorMetrics(
        embedding_backlog_count=int(embedding_count),
        embedding_backlog_oldest_at=embedding_oldest,
        deletion_backlog_count=int(deletion_count),
        deletion_backlog_oldest_at=deletion_oldest,
        users_near_storage_quota=sum(value >= threshold for value in usage.values()),
        stored_bytes=sum(usage.values()),
    )
