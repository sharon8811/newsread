"""Retention and durable object garbage collection for browser history."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .history_storage import EncryptedHistoryStorage, HistoryStorageError
from .models import (
    BrowserHistoryDocument,
    BrowserHistoryGcState,
    BrowserHistoryImage,
    BrowserHistoryObjectDeletion,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
    BrowserHistorySettings,
)

_MANAGED_OBJECT_KEY = re.compile(
    r"^users/[1-9][0-9]*/history/(?:documents|images)/sha256/"
    r"[0-9a-f]{2}/[0-9a-f]{64}$"
)


@dataclass(frozen=True)
class RetentionResult:
    pages_deleted: int = 0
    version_links_deleted: int = 0


@dataclass(frozen=True)
class GarbageCollectionResult:
    documents_deleted: int = 0
    images_deleted: int = 0
    outbox_completed: int = 0
    orphan_objects_deleted: int = 0


async def apply_history_retention(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> RetentionResult:
    """Expire URL rows and old content-version links, repairing current links."""
    now = now or datetime.now(UTC)
    policies = (
        await session.execute(
            select(
                BrowserHistorySettings.user_id,
                BrowserHistorySettings.retention_days,
            ).where(BrowserHistorySettings.retention_days.is_not(None))
        )
    ).all()
    pages_deleted = 0
    links_deleted = 0
    for user_id, retention_days in policies:
        cutoff = now - timedelta(days=retention_days)
        expired_links = (
            await session.execute(
                select(
                    BrowserHistoryPageDocument.id,
                    BrowserHistoryPageDocument.page_id,
                )
                .join(
                    BrowserHistoryPage,
                    BrowserHistoryPage.id == BrowserHistoryPageDocument.page_id,
                )
                .where(
                    BrowserHistoryPage.user_id == user_id,
                    BrowserHistoryPageDocument.last_seen_at < cutoff,
                )
            )
        ).all()
        if expired_links:
            link_ids = [link_id for link_id, _ in expired_links]
            affected_page_ids = {page_id for _, page_id in expired_links}
            result = await session.execute(
                delete(BrowserHistoryPageDocument).where(
                    BrowserHistoryPageDocument.id.in_(link_ids)
                )
            )
            links_deleted += result.rowcount
            for page_id in affected_page_ids:
                page = await session.get(BrowserHistoryPage, page_id)
                if page is None:
                    continue
                page.current_document_id = await session.scalar(
                    select(BrowserHistoryPageDocument.document_id)
                    .where(BrowserHistoryPageDocument.page_id == page_id)
                    .order_by(
                        BrowserHistoryPageDocument.last_seen_at.desc(),
                        BrowserHistoryPageDocument.id.desc(),
                    )
                    .limit(1)
                )

        result = await session.execute(
            delete(BrowserHistoryPage).where(
                BrowserHistoryPage.user_id == user_id,
                BrowserHistoryPage.last_visited_at < cutoff,
            )
        )
        pages_deleted += result.rowcount
    await session.commit()
    return RetentionResult(
        pages_deleted=pages_deleted,
        version_links_deleted=links_deleted,
    )


async def delete_unreferenced_history_rows(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete old unlinked rows; database triggers enqueue their object keys."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.history_object_gc_grace_hours)
    linked_document = (
        select(BrowserHistoryPageDocument.id)
        .where(BrowserHistoryPageDocument.document_id == BrowserHistoryDocument.id)
        .exists()
    )
    document_ids = list(
        await session.scalars(
            select(BrowserHistoryDocument.id)
            .where(
                BrowserHistoryDocument.created_at <= cutoff,
                ~linked_document,
            )
            .order_by(BrowserHistoryDocument.id)
            .limit(settings.history_object_delete_batch)
        )
    )
    documents_deleted = 0
    if document_ids:
        result = await session.execute(
            delete(BrowserHistoryDocument).where(BrowserHistoryDocument.id.in_(document_ids))
        )
        documents_deleted = result.rowcount

    page_image = (
        select(BrowserHistoryPage.id)
        .where(BrowserHistoryPage.favicon_image_id == BrowserHistoryImage.id)
        .exists()
    )
    document_image = (
        select(BrowserHistoryDocument.id)
        .where(BrowserHistoryDocument.lead_image_id == BrowserHistoryImage.id)
        .exists()
    )
    image_ids = list(
        await session.scalars(
            select(BrowserHistoryImage.id)
            .where(
                BrowserHistoryImage.created_at <= cutoff,
                ~page_image,
                ~document_image,
            )
            .order_by(BrowserHistoryImage.id)
            .limit(settings.history_object_delete_batch)
        )
    )
    images_deleted = 0
    if image_ids:
        result = await session.execute(
            delete(BrowserHistoryImage).where(BrowserHistoryImage.id.in_(image_ids))
        )
        images_deleted = result.rowcount
    await session.commit()
    return documents_deleted, images_deleted


async def process_object_deletion_outbox(
    session: AsyncSession,
    storage: EncryptedHistoryStorage,
    *,
    now: datetime | None = None,
) -> int:
    """Delete queued objects with idempotent retries and bounded backoff."""
    now = now or datetime.now(UTC)
    rows = list(
        await session.scalars(
            select(BrowserHistoryObjectDeletion)
            .where(
                or_(
                    BrowserHistoryObjectDeletion.next_attempt_at.is_(None),
                    BrowserHistoryObjectDeletion.next_attempt_at <= now,
                )
            )
            .order_by(
                BrowserHistoryObjectDeletion.queued_at,
                BrowserHistoryObjectDeletion.id,
            )
            .with_for_update(skip_locked=True)
            .limit(settings.history_object_delete_batch)
        )
    )
    completed = 0
    for row in rows:
        try:
            await storage.delete(row.object_key)
        except HistoryStorageError as exc:
            row.attempts += 1
            row.last_error = str(exc)[:2_000]
            delay_minutes = min(24 * 60, 2 ** min(row.attempts, 10))
            row.next_attempt_at = now + timedelta(minutes=delay_minutes)
        else:
            await session.delete(row)
            completed += 1
    await session.commit()
    return completed


async def sweep_orphaned_history_objects(
    session: AsyncSession,
    storage: EncryptedHistoryStorage,
    *,
    now: datetime | None = None,
) -> int:
    """Delete old managed bucket objects that have no database owner row."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.history_object_gc_grace_hours)
    await session.execute(
        pg_insert(BrowserHistoryGcState)
        .values(name="object_sweep")
        .on_conflict_do_nothing(index_elements=["name"])
    )
    state = await session.scalar(
        select(BrowserHistoryGcState)
        .where(BrowserHistoryGcState.name == "object_sweep")
        .with_for_update()
    )
    page = await storage.object_store.list_objects(
        "users/",
        limit=settings.history_object_gc_scan_limit,
        cursor=state.cursor,
    )
    referenced = set(await session.scalars(select(BrowserHistoryDocument.object_key)))
    referenced.update(await session.scalars(select(BrowserHistoryImage.object_key)))
    deleted = 0
    for item in page.objects:
        if (
            item.key in referenced
            or item.modified_at > cutoff
            or _MANAGED_OBJECT_KEY.fullmatch(item.key) is None
        ):
            continue
        await storage.delete(item.key)
        deleted += 1
    state.cursor = page.next_cursor
    await session.commit()
    return deleted


async def collect_history_garbage(
    session: AsyncSession,
    *,
    storage: EncryptedHistoryStorage | None,
    now: datetime | None = None,
) -> GarbageCollectionResult:
    """Run the database and object-store halves of one bounded GC cycle."""
    documents_deleted, images_deleted = await delete_unreferenced_history_rows(
        session,
        now=now,
    )
    if storage is None:
        return GarbageCollectionResult(
            documents_deleted=documents_deleted,
            images_deleted=images_deleted,
        )
    outbox_completed = await process_object_deletion_outbox(
        session,
        storage,
        now=now,
    )
    orphan_objects_deleted = await sweep_orphaned_history_objects(
        session,
        storage,
        now=now,
    )
    return GarbageCollectionResult(
        documents_deleted=documents_deleted,
        images_deleted=images_deleted,
        outbox_completed=outbox_completed,
        orphan_objects_deleted=orphan_objects_deleted,
    )
