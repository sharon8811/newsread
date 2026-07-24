from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app import history_gc
from app.history_storage import EncryptedHistoryStorage, InMemoryObjectStore
from app.models import (
    BrowserHistoryDocument,
    BrowserHistoryObjectDeletion,
    BrowserHistoryPage,
    BrowserHistoryPageDocument,
    BrowserHistorySettings,
)


def _document(
    user_id: int,
    content_hash: str,
    *,
    created_at: datetime,
) -> BrowserHistoryDocument:
    return BrowserHistoryDocument(
        user_id=user_id,
        content_hash=content_hash,
        object_key=(f"users/{user_id}/history/documents/sha256/{content_hash[:2]}/{content_hash}"),
        storage_status="ready",
        byte_size=100,
        character_count=80,
        extraction_version="history-dom-v2",
        created_at=created_at,
    )


async def test_version_retention_repairs_current_document_and_deletes_object(
    session,
    users,
):
    now = datetime.now(UTC)
    user = await users.create()
    session.add(BrowserHistorySettings(user_id=user.id, retention_days=30))
    expired = _document(user.id, "a" * 64, created_at=now - timedelta(days=40))
    current = _document(user.id, "b" * 64, created_at=now - timedelta(days=2))
    page = BrowserHistoryPage(
        user_id=user.id,
        url_hash="c" * 64,
        url="https://example.com/article",
        hostname="example.com",
        first_visited_at=now - timedelta(days=40),
        last_visited_at=now - timedelta(days=1),
        visit_count=2,
    )
    session.add_all([expired, current, page])
    await session.flush()
    page.current_document_id = expired.id
    session.add_all(
        [
            BrowserHistoryPageDocument(
                page_id=page.id,
                document_id=expired.id,
                first_seen_at=now - timedelta(days=40),
                last_seen_at=now - timedelta(days=31),
            ),
            BrowserHistoryPageDocument(
                page_id=page.id,
                document_id=current.id,
                first_seen_at=now - timedelta(days=2),
                last_seen_at=now - timedelta(days=1),
            ),
        ]
    )
    await session.commit()

    retained = await history_gc.apply_history_retention(session, now=now)
    assert retained.pages_deleted == 0
    assert retained.version_links_deleted == 1
    await session.refresh(page)
    assert page.current_document_id == current.id

    deleted_documents, deleted_images = await history_gc.delete_unreferenced_history_rows(
        session, now=now
    )
    assert (deleted_documents, deleted_images) == (1, 0)
    assert await session.get(BrowserHistoryDocument, expired.id) is None
    assert await session.get(BrowserHistoryDocument, current.id) is not None

    deletion = await session.scalar(select(BrowserHistoryObjectDeletion))
    assert deletion is not None
    assert deletion.object_key == expired.object_key

    store = InMemoryObjectStore(
        {
            expired.object_key: b"expired ciphertext",
            current.object_key: b"current ciphertext",
        }
    )
    storage = EncryptedHistoryStorage(store, key_service=None)  # type: ignore[arg-type]
    assert (
        await history_gc.process_object_deletion_outbox(
            session,
            storage,
            now=now,
        )
        == 1
    )
    assert expired.object_key not in store.objects
    assert current.object_key in store.objects
    assert await session.scalar(select(func.count()).select_from(BrowserHistoryObjectDeletion)) == 0


async def test_orphan_sweep_is_grace_bounded_and_namespace_restricted(
    session,
    users,
):
    now = datetime.now(UTC)
    user = await users.create()
    referenced = _document(
        user.id,
        "d" * 64,
        created_at=now - timedelta(days=2),
    )
    session.add(referenced)
    await session.commit()

    orphan_hash = "e" * 64
    recent_hash = "f" * 64
    orphan_key = f"users/{user.id}/history/documents/sha256/ee/{orphan_hash}"
    recent_key = f"users/{user.id}/history/documents/sha256/ff/{recent_hash}"
    foreign_key = f"users/{user.id}/exports/archive.zip"
    store = InMemoryObjectStore(
        {
            referenced.object_key: b"referenced",
            orphan_key: b"orphan",
            recent_key: b"recent",
            foreign_key: b"not managed by history GC",
        }
    )
    old = now - timedelta(days=2)
    store.modified_at.update(
        {
            referenced.object_key: old,
            orphan_key: old,
            recent_key: now,
            foreign_key: old,
        }
    )
    storage = EncryptedHistoryStorage(store, key_service=None)  # type: ignore[arg-type]

    assert (
        await history_gc.sweep_orphaned_history_objects(
            session,
            storage,
            now=now,
        )
        == 1
    )
    assert set(store.objects) == {
        referenced.object_key,
        recent_key,
        foreign_key,
    }


async def test_orphan_sweep_paginates_without_starving_later_keys(
    session,
    users,
    monkeypatch,
):
    now = datetime.now(UTC)
    user = await users.create()
    keys = [
        f"users/{user.id}/history/documents/sha256/{value * 2}/{value * 64}"
        for value in ("1", "2", "3")
    ]
    store = InMemoryObjectStore({key: b"orphan" for key in keys})
    store.modified_at = {key: now - timedelta(days=2) for key in keys}
    storage = EncryptedHistoryStorage(store, key_service=None)  # type: ignore[arg-type]
    monkeypatch.setattr(history_gc.settings, "history_object_gc_scan_limit", 2)

    assert (
        await history_gc.sweep_orphaned_history_objects(
            session,
            storage,
            now=now,
        )
        == 2
    )
    assert (
        await history_gc.sweep_orphaned_history_objects(
            session,
            storage,
            now=now,
        )
        == 1
    )
    assert store.objects == {}
